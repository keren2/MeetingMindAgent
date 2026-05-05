from __future__ import annotations

from pathlib import Path
import re
import shutil
from uuid import uuid4

from fastapi import UploadFile

from .config import UPLOAD_DIR
from .db import connect, now_iso, row_to_dict


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        chunks.append(normalized[start : start + size])
        start += max(1, size - overlap)
    return chunks


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        except Exception:
            return ""
    if suffix == ".docx":
        try:
            from docx import Document

            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            return ""
    return path.read_text(encoding="utf-8", errors="ignore")


async def save_upload(user_id: int, upload: UploadFile) -> dict:
    user_dir = UPLOAD_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid4().hex}_{Path(upload.filename or 'upload.txt').name}"
    stored = user_dir / safe_name
    with stored.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    text = extract_text(stored)
    chunks = chunk_text(text)
    with connect() as db:
        cur = db.execute(
            "INSERT INTO kb_files(user_id, filename, stored_path, created_at) VALUES(?, ?, ?, ?)",
            (user_id, upload.filename or safe_name, str(stored), now_iso()),
        )
        file_id = cur.lastrowid
        for chunk in chunks:
            db.execute(
                "INSERT INTO kb_chunks(file_id, user_id, content, created_at) VALUES(?, ?, ?, ?)",
                (file_id, user_id, chunk, now_iso()),
            )
    return {"id": file_id, "filename": upload.filename, "chunks": len(chunks)}


def list_files(user_id: int) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT f.id, f.filename, f.created_at, COUNT(c.id) AS chunks
            FROM kb_files f
            LEFT JOIN kb_chunks c ON c.file_id = f.id
            WHERE f.user_id = ?
            GROUP BY f.id
            ORDER BY f.id DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_file(user_id: int, file_id: int) -> None:
    with connect() as db:
        row = row_to_dict(db.execute("SELECT stored_path FROM kb_files WHERE id=? AND user_id=?", (file_id, user_id)).fetchone())
        if row:
            Path(row["stored_path"]).unlink(missing_ok=True)
        db.execute("DELETE FROM kb_files WHERE id=? AND user_id=?", (file_id, user_id))


def search_knowledge(user_id: int, query: str, limit: int = 4) -> list[dict]:
    terms = [t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(t) > 1]
    with connect() as db:
        rows = db.execute(
            """
            SELECT c.id, c.content, f.filename
            FROM kb_chunks c
            JOIN kb_files f ON f.id = c.file_id
            WHERE c.user_id = ?
            ORDER BY c.id DESC
            LIMIT 80
            """,
            (user_id,),
        ).fetchall()
    scored = []
    for row in rows:
        content = row["content"]
        low = content.lower()
        score = sum(low.count(term) for term in terms)
        if score or not terms:
            scored.append({"id": row["id"], "filename": row["filename"], "content": content, "score": score})
    return sorted(scored, key=lambda x: (x["score"], x["id"]), reverse=True)[:limit]
