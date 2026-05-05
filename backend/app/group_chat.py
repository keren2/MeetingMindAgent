from __future__ import annotations

from pathlib import Path
import asyncio
import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .auth import current_user
from .config import REPORT_DIR
from .db import connect, now_iso, row_to_dict
from .llm import call_llm
from .reports import save_report
from .skill_loader import loaded_skills, public_skill_summary, skill_prompt_for_persona
from .usage import consume_usage, usage_status


router = APIRouter()


STEVE_JOBS_AVATAR = "https://so1.360tres.com/t01029d47bcc47f598a.jpg"
ELON_MUSK_AVATAR = "https://x0.ifengimg.com/ucms/2022_18/907DCA5393E1BC4EF94D592E5888E560044245E6_size48_w1217_h686.jpg"
DONALD_TRUMP_AVATAR = "https://commons.wikimedia.org/wiki/Special:Redirect/file/Donald%20Trump%20official%20portrait.jpg?width=160"
NETANYAHU_AVATAR = "https://commons.wikimedia.org/wiki/Special:Redirect/file/Netanyahu%20official%20portrait.jpg?width=160"
RANDOM_AVATAR_URL = "https://i.pravatar.cc/160?u="


DEFAULT_PERSONAS = [
    {
        "name": "马斯克",
        "background": "埃隆·马斯克不做特斯拉 CEO 了，现在来到这里担任资深程序员。关注第一性原理、极限工程效率、系统架构、自动化、性能、安全、可维护性和交付风险。",
        "avatar": ELON_MUSK_AVATAR,
        "tone": "马斯克式表达，直接、工程化、第一性原理、偏向快速验证和高强度执行",
    },
    {
        "name": "乔布斯",
        "background": "史蒂夫·乔布斯来到这里担任产品经理，负责产品体验、用户价值、需求取舍、发布叙事、里程碑、验收标准和跨团队协作。",
        "avatar": STEVE_JOBS_AVATAR,
        "tone": "乔布斯式表达，极致聚焦、追求简洁、强调产品体验，会直接指出什么该砍掉",
    },
    {
        "name": "特朗普",
        "background": "唐纳德·特朗普来到这里担任销售，长期面对客户和市场，关注客户痛点、购买理由、竞品差异、定价包装、成交阻力和交易叙事。",
        "avatar": DONALD_TRUMP_AVATAR,
        "tone": "特朗普式中文表达，自信、短句、强调最好和最大，善于把方案包装成强交易故事",
    },
]


DEFAULT_PERSONA_MIGRATIONS = {
    "资深程序员": DEFAULT_PERSONAS[0],
    "产品公司经理": DEFAULT_PERSONAS[1],
    "销售": DEFAULT_PERSONAS[2],
}


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    background: str = Field(min_length=4, max_length=1200)
    avatar: str = Field(default="", max_length=500)
    tone: str = Field(default="", max_length=400)


class GroupStartIn(BaseModel):
    event: str = Field(min_length=4, max_length=3000)
    role_ids: list[int] = Field(min_length=1)
    title: str = Field(default="", max_length=80)
    rounds: int = Field(default=1, ge=1, le=5)


class ExportPathIn(BaseModel):
    directory: str = Field(min_length=1, max_length=500)
    filename: str = Field(default="", max_length=120)


class ImportAnalyzeIn(BaseModel):
    title: str = Field(default="聊天记录分析总结", max_length=80)
    content: str = Field(min_length=1, max_length=80000)
    mode: str = Field(default="detailed", pattern="^(quick|detailed)$")


def ensure_default_personas(user_id: int) -> None:
    with connect() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM personas WHERE user_id=?", (user_id,)).fetchone()["n"]
        if count:
            for old_name, persona in DEFAULT_PERSONA_MIGRATIONS.items():
                db.execute(
                    """
                    UPDATE personas
                    SET name=?, background=?, avatar=?, tone=?
                    WHERE user_id=? AND name=?
                    """,
                    (persona["name"], persona["background"], persona["avatar"], persona["tone"], user_id, old_name),
                )
            for persona in DEFAULT_PERSONAS:
                exists = db.execute(
                    "SELECT id FROM personas WHERE user_id=? AND name=?",
                    (user_id, persona["name"]),
                ).fetchone()
                if not exists:
                    db.execute(
                        "INSERT INTO personas(user_id, name, background, avatar, tone, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                        (user_id, persona["name"], persona["background"], persona["avatar"], persona["tone"], now_iso()),
                    )
                else:
                    db.execute(
                        "UPDATE personas SET background=?, avatar=?, tone=? WHERE user_id=? AND name=?",
                        (persona["background"], persona["avatar"], persona["tone"], user_id, persona["name"]),
                    )
            return
        for persona in DEFAULT_PERSONAS:
            db.execute(
                "INSERT INTO personas(user_id, name, background, avatar, tone, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, persona["name"], persona["background"], persona["avatar"], persona["tone"], now_iso()),
            )


def enrich_persona(persona: dict) -> dict:
    persona["matched_skill"] = public_skill_summary(persona)
    return persona


def persona_rows(user_id: int, role_ids: list[int] | None = None) -> list[dict]:
    ensure_default_personas(user_id)
    with connect() as db:
        if role_ids:
            placeholders = ",".join("?" for _ in role_ids)
            rows = db.execute(
                f"""
                SELECT * FROM personas
                WHERE user_id=? AND id IN ({placeholders})
                ORDER BY CASE name
                    WHEN '乔布斯' THEN 1
                    WHEN '马斯克' THEN 2
                    WHEN '特朗普' THEN 3
                    ELSE 10
                END, id ASC
                """,
                (user_id, *role_ids),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT * FROM personas
                WHERE user_id=?
                ORDER BY CASE name
                    WHEN '乔布斯' THEN 1
                    WHEN '马斯克' THEN 2
                    WHEN '特朗普' THEN 3
                    ELSE 10
                END, id ASC
                """,
                (user_id,),
            ).fetchall()
    return [enrich_persona(dict(row)) for row in rows]


def group_messages_markdown(group: dict, messages: list[dict]) -> str:
    lines = [
        f"# {group['title']}",
        "",
        f"- 讨论事件：{group['event']}",
        f"- 创建时间：{group['created_at']}",
        "",
        "## 聊天记录",
        "",
    ]
    for message in messages:
        lines.append(f"### {message['avatar']} {message['speaker']} · {message['created_at']}")
        lines.append("")
        lines.append(message["content"])
        lines.append("")
    return "\n".join(lines)


def safe_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return (cleaned or fallback)[:100]


def normalize_avatar(avatar: str, seed: str) -> str:
    avatar = (avatar or "").strip()
    if avatar.startswith("http://") or avatar.startswith("https://"):
        return avatar
    return f"{RANDOM_AVATAR_URL}{safe_filename(seed, 'agent')}"


def ndjson_event(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def insert_group_message(group_id: int, speaker: str, avatar: str, content: str, persona_id: int | None = None) -> dict:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO group_messages(group_id, persona_id, speaker, avatar, content, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (group_id, persona_id, speaker, avatar, content, now_iso()),
        )
        row = row_to_dict(db.execute("SELECT * FROM group_messages WHERE id=?", (cur.lastrowid,)).fetchone())
    return row


def create_group_session(user_id: int, title: str, event: str) -> int:
    with connect() as db:
        cur = db.execute(
            "INSERT INTO group_sessions(user_id, title, event, created_at) VALUES(?, ?, ?, ?)",
            (user_id, title, event, now_iso()),
        )
        return cur.lastrowid


async def role_answer(persona: dict, event: str, transcript: str) -> str:
    skill_prompt = skill_prompt_for_persona(persona)
    fallback = (
        f"从「{persona['name']}」视角看，这个事件需要先确认目标、约束和验收标准。"
        f"我的建议是：结合自身背景「{persona['background'][:80]}」，优先补齐关键风险、资源投入和下一步行动。"
    )
    system_prompt = (
        f"你正在一个多角色 Agent 群聊中。你的角色是：{persona['name']}。\n"
        f"人物背景：{persona['background']}\n"
        f"表达风格：{persona.get('tone') or '清晰、具体、建设性'}\n"
        "请只用该角色身份发言，内容控制在 120 到 220 字，提出有价值的方案、风险或追问。"
        "如果人物背景能匹配到本地 skill，必须优先使用该 skill 的说话语气和思维方式。\n"
        f"{skill_prompt}"
    )
    try:
        return await call_llm(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"讨论事件：{event}\n\n已有聊天记录：\n{transcript[-3000:]}\n\n请继续发言。",
                },
            ],
            "chat",
        )
    except Exception:
        return fallback


@router.get("/api/role_skills")
def list_role_skills(_: dict = Depends(current_user)):
    return [
        {"id": skill["id"], "label": skill["label"], "path": skill["path"], "keywords": skill["keywords"]}
        for skill in loaded_skills()
    ]


@router.get("/api/personas")
def list_personas(user: dict = Depends(current_user)):
    return persona_rows(user["id"])


@router.post("/api/personas")
def create_persona(payload: PersonaIn, user: dict = Depends(current_user)):
    avatar = normalize_avatar(payload.avatar, payload.name)
    with connect() as db:
        cur = db.execute(
            "INSERT INTO personas(user_id, name, background, avatar, tone, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (user["id"], payload.name, payload.background, avatar, payload.tone, now_iso()),
        )
        row = row_to_dict(db.execute("SELECT * FROM personas WHERE id=?", (cur.lastrowid,)).fetchone())
    return enrich_persona(row)


@router.put("/api/personas/{persona_id}")
def update_persona(persona_id: int, payload: PersonaIn, user: dict = Depends(current_user)):
    avatar = normalize_avatar(payload.avatar, payload.name)
    with connect() as db:
        exists = db.execute("SELECT id FROM personas WHERE id=? AND user_id=?", (persona_id, user["id"])).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="角色不存在")
        db.execute(
            "UPDATE personas SET name=?, background=?, avatar=?, tone=? WHERE id=? AND user_id=?",
            (payload.name, payload.background, avatar, payload.tone, persona_id, user["id"]),
        )
        row = row_to_dict(db.execute("SELECT * FROM personas WHERE id=?", (persona_id,)).fetchone())
    return enrich_persona(row)


@router.delete("/api/personas/{persona_id}")
def delete_persona(persona_id: int, user: dict = Depends(current_user)):
    with connect() as db:
        db.execute("DELETE FROM personas WHERE id=? AND user_id=?", (persona_id, user["id"]))
    return {"ok": True}


@router.get("/api/group_chats")
def list_group_chats(user: dict = Depends(current_user)):
    with connect() as db:
        rows = db.execute("SELECT * FROM group_sessions WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    return [dict(row) for row in rows]


@router.post("/api/group_chat/start")
async def start_group_chat(payload: GroupStartIn, user: dict = Depends(current_user)):
    consume_usage(user["id"], "group_discussion")
    roles = persona_rows(user["id"], payload.role_ids)
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(status_code=404, detail="存在不可用角色")
    title = payload.title or payload.event[:36] or "角色群聊"
    group_id = create_group_session(user["id"], title, payload.event)
    insert_group_message(group_id, "老板", NETANYAHU_AVATAR, f"讨论事件：{payload.event}")
    transcript = f"老板：讨论事件：{payload.event}\n"
    for _ in range(payload.rounds):
        for persona in roles:
            content = await role_answer(persona, payload.event, transcript)
            transcript += f"\n{persona['name']}：{content}"
            insert_group_message(group_id, persona["name"], persona["avatar"], content, persona["id"])
    return {
        "group_id": group_id,
        "messages": get_group_messages(group_id, user),
        "matched_skills": [
            {"persona": role["name"], "skill": role["matched_skill"]}
            for role in roles
            if role.get("matched_skill")
        ],
        "usage": usage_status(user["id"]),
    }


@router.post("/api/group_chat/start_stream")
async def start_group_chat_stream(payload: GroupStartIn, user: dict = Depends(current_user)):
    consume_usage(user["id"], "group_discussion")
    roles = persona_rows(user["id"], payload.role_ids)
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(status_code=404, detail="存在不可用角色")
    title = payload.title or payload.event[:36] or "角色群聊"
    group_id = create_group_session(user["id"], title, payload.event)

    async def stream():
        yield ndjson_event({"type": "group", "group_id": group_id})
        boss = insert_group_message(group_id, "老板", NETANYAHU_AVATAR, f"讨论事件：{payload.event}")
        yield ndjson_event({"type": "message_done", "message": boss})
        transcript = f"老板：讨论事件：{payload.event}\n"
        matched = [
            {"persona": role["name"], "skill": role["matched_skill"]}
            for role in roles
            if role.get("matched_skill")
        ]
        yield ndjson_event({"type": "matched_skills", "matched_skills": matched})
        for _ in range(payload.rounds):
            for persona in roles:
                shell = {
                    "id": 0,
                    "group_id": group_id,
                    "persona_id": persona["id"],
                    "speaker": persona["name"],
                    "avatar": persona["avatar"],
                    "content": "",
                    "created_at": now_iso(),
                }
                yield ndjson_event({"type": "message_start", "message": shell})
                content = await role_answer(persona, payload.event, transcript)
                for index in range(0, len(content), 3):
                    yield ndjson_event({"type": "message_delta", "content": content[index : index + 3]})
                    await asyncio.sleep(0.008)
                message = insert_group_message(group_id, persona["name"], persona["avatar"], content, persona["id"])
                transcript += f"\n{persona['name']}：{content}"
                yield ndjson_event({"type": "message_done", "message": message})
        yield ndjson_event({"type": "done", "usage": usage_status(user["id"])})

    return StreamingResponse(stream(), media_type="application/x-ndjson; charset=utf-8")


@router.get("/api/group_chat/{group_id}/messages")
def get_group_messages(group_id: int, user: dict = Depends(current_user)):
    with connect() as db:
        group = db.execute("SELECT id FROM group_sessions WHERE id=? AND user_id=?", (group_id, user["id"])).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="群聊不存在")
        rows = db.execute("SELECT * FROM group_messages WHERE group_id=? ORDER BY id ASC", (group_id,)).fetchall()
    return [dict(row) for row in rows]


@router.delete("/api/group_chat/{group_id}/messages")
def clear_group_messages(group_id: int, user: dict = Depends(current_user)):
    with connect() as db:
        group = db.execute("SELECT id FROM group_sessions WHERE id=? AND user_id=?", (group_id, user["id"])).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="群聊不存在")
        db.execute("DELETE FROM group_messages WHERE group_id=?", (group_id,))
    return {"ok": True}


@router.get("/api/group_chat/{group_id}/export")
def export_group_chat(group_id: int, user: dict = Depends(current_user)):
    with connect() as db:
        group = row_to_dict(db.execute("SELECT * FROM group_sessions WHERE id=? AND user_id=?", (group_id, user["id"])).fetchone())
        if not group:
            raise HTTPException(status_code=404, detail="群聊不存在")
        rows = [dict(row) for row in db.execute("SELECT * FROM group_messages WHERE group_id=? ORDER BY id ASC", (group_id,)).fetchall()]
    path = REPORT_DIR / f"group-chat-{group_id}.md"
    path.write_text(group_messages_markdown(group, rows), encoding="utf-8")
    return FileResponse(path, filename=f"group-chat-{group_id}.md", media_type="text/markdown")


@router.post("/api/group_chat/{group_id}/export_to_path")
def export_group_chat_to_path(group_id: int, payload: ExportPathIn, user: dict = Depends(current_user)):
    with connect() as db:
        group = row_to_dict(db.execute("SELECT * FROM group_sessions WHERE id=? AND user_id=?", (group_id, user["id"])).fetchone())
        if not group:
            raise HTTPException(status_code=404, detail="群聊不存在")
        rows = [dict(row) for row in db.execute("SELECT * FROM group_messages WHERE group_id=? ORDER BY id ASC", (group_id,)).fetchall()]
    directory = Path(payload.directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(payload.filename or f"group-chat-{group_id}", f"group-chat-{group_id}")
    if not filename.lower().endswith(".md"):
        filename += ".md"
    path = directory / filename
    path.write_text(group_messages_markdown(group, rows), encoding="utf-8")
    return {"ok": True, "path": str(path)}


@router.post("/api/group_chat/import_analyze")
async def import_analyze(payload: ImportAnalyzeIn, user: dict = Depends(current_user)):
    consume_usage(user["id"], "import_analyze")
    system_prompt = (
        "你是高级产品经理，请根据聊天记录输出结构化总结，包含共识、分歧、方案、风险、待确认问题和行动项。"
        if payload.mode == "detailed"
        else "请把本次聊天内容总结成非常简洁的信息，只保留最重要的结论、决定和待办。控制在 5 条以内，每条一句话，不要展开分析。"
    )
    markdown = await call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload.content[-50000:]},
        ],
        "report",
    )
    report = save_report(user["id"], None, markdown, payload.title)
    return {**report, "usage": usage_status(user["id"])}


@router.post("/api/group_chat/import_file_analyze")
async def import_file_analyze(file: UploadFile = File(...), user: dict = Depends(current_user)):
    data = await file.read()
    content = data.decode("utf-8", errors="ignore")
    return await import_analyze(ImportAnalyzeIn(title=file.filename or "聊天记录分析总结", content=content), user)
