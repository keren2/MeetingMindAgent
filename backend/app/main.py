from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .ai_stack import rag_answer_chain
from .agents import run_workflow
from .auth import admin_user, current_user
from .config import settings
from .db import connect, init_db, now_iso, row_to_dict
from .group_chat import router as group_chat_router
from .llm import call_llm
from .rag import delete_file, ensure_vector_index, format_documents, list_files, retriever_for_user, save_upload
from .reports import save_report
from .security import create_token, hash_password, verify_password
from .usage import consume_usage, ensure_limit_row, usage_status


app = FastAPI(title="MeetingMind Agent Pro API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(group_chat_router)


class AuthIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=80)


class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


class ReportIn(BaseModel):
    session_id: int | None = None
    prompt: str = ""


class ConfigIn(BaseModel):
    default_daily_limit: int = Field(ge=1, le=9999)


class ResetLimitIn(BaseModel):
    user_id: int
    daily_limit: int | None = Field(default=None, ge=1, le=9999)


@app.on_event("startup")
def startup() -> None:
    init_db()
    ensure_vector_index()


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings()["app_name"]}


@app.post("/api/register")
def register(payload: AuthIn):
    with connect() as db:
        exists = db.execute("SELECT id FROM users WHERE username=?", (payload.username,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="用户名已存在")
        cur = db.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)",
            (payload.username, hash_password(payload.password), now_iso()),
        )
        ensure_limit_row(db, cur.lastrowid)
        token = create_token(cur.lastrowid)
    return {"token": token}


@app.post("/api/login")
def login(payload: AuthIn):
    with connect() as db:
        user = row_to_dict(db.execute("SELECT * FROM users WHERE username=?", (payload.username,)).fetchone())
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="用户已被禁用")
    return {"token": create_token(user["id"])}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "is_admin": bool(user["is_admin"]),
        "usage": usage_status(user["id"]),
    }


@app.get("/api/sessions")
def sessions(user: dict = Depends(current_user)):
    with connect() as db:
        rows = db.execute("SELECT * FROM sessions WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/messages/{session_id}")
def messages(session_id: int, user: dict = Depends(current_user)):
    with connect() as db:
        session = db.execute("SELECT id FROM sessions WHERE id=? AND user_id=?", (session_id, user["id"])).fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        rows = db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id ASC", (session_id,)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/chat")
async def chat(payload: ChatIn, user: dict = Depends(current_user)):
    consume_usage(user["id"], "chat")
    with connect() as db:
        if payload.session_id:
            session = db.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (payload.session_id, user["id"])).fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
            session_id = payload.session_id
        else:
            title = payload.message[:24] or "新会议"
            cur = db.execute(
                "INSERT INTO sessions(user_id, title, created_at) VALUES(?, ?, ?)",
                (user["id"], title, now_iso()),
            )
            session_id = cur.lastrowid
        db.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?, 'user', ?, ?)",
            (session_id, payload.message, now_iso()),
        )
        rows = db.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id ASC", (session_id,)).fetchall()
        history = [dict(r) for r in rows]
    rag_documents = retriever_for_user(user["id"]).invoke(payload.message)
    rag_docs = [
        {
            "id": int(doc.metadata.get("id", 0)),
            "filename": str(doc.metadata.get("filename", "")),
            "content": doc.page_content,
            "score": float(doc.metadata.get("score", 0.0)),
        }
        for doc in rag_documents
    ]
    question = "\n".join(item["content"] for item in history[-6:])
    try:
        answer = await rag_answer_chain(
            "你是 AI 需求会议助手，请结合检索上下文和多轮记忆，帮助用户梳理需求、风险、待确认问题和下一步行动。"
        ).ainvoke({"context": format_documents(rag_documents), "question": question})
    except Exception:
        answer = await call_llm(
            [
                {"role": "system", "content": "你是 AI 需求会议助手，请帮助用户梳理需求、风险和下一步行动。"},
                *history[-10:],
                {"role": "user", "content": f"可参考知识库：\n{format_documents(rag_documents)}\n\n当前问题：{payload.message}"},
            ],
            "chat",
        )
    with connect() as db:
        db.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?, 'assistant', ?, ?)",
            (session_id, answer, now_iso()),
        )
    return {"session_id": session_id, "answer": answer, "rag_docs": rag_docs, "usage": usage_status(user["id"])}


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatIn, user: dict = Depends(current_user)):
    result = await chat(payload, user)

    async def generator():
        for char in result["answer"]:
            yield char

    return StreamingResponse(generator(), media_type="text/plain; charset=utf-8")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), user: dict = Depends(current_user)):
    return await save_upload(user["id"], file)


@app.get("/api/files")
def files(user: dict = Depends(current_user)):
    return list_files(user["id"])


@app.delete("/api/file/{file_id}")
def remove_file(file_id: int, user: dict = Depends(current_user)):
    delete_file(user["id"], file_id)
    return {"ok": True}


@app.post("/api/generate_report")
async def generate_report(payload: ReportIn, user: dict = Depends(current_user)):
    consume_usage(user["id"], "generate_report")
    with connect() as db:
        rows = []
        if payload.session_id:
            session = db.execute("SELECT id FROM sessions WHERE id=? AND user_id=?", (payload.session_id, user["id"])).fetchone()
            if not session:
                raise HTTPException(status_code=404, detail="会话不存在")
            rows = db.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id ASC", (payload.session_id,)).fetchall()
    messages = [dict(r) for r in rows] or [{"role": "user", "content": payload.prompt or "请生成项目需求分析报告"}]
    workflow = await run_workflow(user["id"], messages, payload.prompt or "\n".join(m["content"] for m in messages[-4:]), "report")
    report = save_report(user["id"], payload.session_id, workflow["answer"])
    return {**report, "trace": workflow.get("trace", []), "usage": usage_status(user["id"])}


@app.get("/api/report/{report_id}")
def get_report(report_id: int, user: dict = Depends(current_user)):
    with connect() as db:
        row = row_to_dict(db.execute("SELECT * FROM reports WHERE id=? AND user_id=?", (report_id, user["id"])).fetchone())
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    return row


@app.get("/api/export/md")
def export_md(report_id: int, user: dict = Depends(current_user)):
    report = get_report(report_id, user)
    path = Path(report["pdf_path"]).with_suffix(".md")
    path.write_text(report["markdown"], encoding="utf-8")
    return FileResponse(path, filename=f"meetingmind-report-{report_id}.md", media_type="text/markdown")


@app.get("/api/export/pdf")
def export_pdf(report_id: int, user: dict = Depends(current_user)):
    report = get_report(report_id, user)
    path = Path(report["pdf_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在")
    return FileResponse(path, filename=f"meetingmind-report-{report_id}.pdf", media_type="application/pdf")


@app.get("/admin/users")
def admin_users(_: dict = Depends(admin_user)):
    with connect() as db:
        rows = db.execute(
            """
            SELECT u.id, u.username, u.created_at, u.is_active, u.is_admin,
                   l.daily_limit, l.used_today, l.last_reset_date
            FROM users u
            LEFT JOIN usage_limits l ON l.user_id = u.id
            ORDER BY u.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/admin/usage")
def admin_usage(_: dict = Depends(admin_user)):
    with connect() as db:
        rows = db.execute(
            """
            SELECT logs.id, logs.type, logs.created_at, users.username
            FROM usage_logs logs
            JOIN users ON users.id = logs.user_id
            ORDER BY logs.id DESC
            LIMIT 200
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/reset_limit")
def reset_limit(payload: ResetLimitIn, _: dict = Depends(admin_user)):
    with connect() as db:
        ensure_limit_row(db, payload.user_id)
        if payload.daily_limit:
            db.execute(
                "UPDATE usage_limits SET daily_limit=?, used_today=0, last_reset_date=date('now') WHERE user_id=?",
                (payload.daily_limit, payload.user_id),
            )
        else:
            db.execute("UPDATE usage_limits SET used_today=0, last_reset_date=date('now') WHERE user_id=?", (payload.user_id,))
    return {"ok": True}


@app.get("/admin/config")
def admin_config(_: dict = Depends(admin_user)):
    with connect() as db:
        rows = db.execute("SELECT key, value FROM system_config").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.post("/admin/config")
def update_config(payload: ConfigIn, _: dict = Depends(admin_user)):
    with connect() as db:
        db.execute(
            "INSERT INTO system_config(key, value) VALUES('default_daily_limit', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(payload.default_daily_limit),),
        )
    return {"ok": True, "default_daily_limit": payload.default_daily_limit}
