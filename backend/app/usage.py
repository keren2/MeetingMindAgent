from __future__ import annotations

from datetime import date

from fastapi import HTTPException

from .db import connect, now_iso, row_to_dict


def ensure_limit_row(db, user_id: int) -> dict:
    row = row_to_dict(db.execute("SELECT * FROM usage_limits WHERE user_id = ?", (user_id,)).fetchone())
    if row:
        return row
    default_limit = int(db.execute("SELECT value FROM system_config WHERE key='default_daily_limit'").fetchone()["value"])
    db.execute(
        "INSERT INTO usage_limits(user_id, daily_limit, used_today, last_reset_date) VALUES(?, ?, 0, ?)",
        (user_id, default_limit, date.today().isoformat()),
    )
    return {"user_id": user_id, "daily_limit": default_limit, "used_today": 0, "last_reset_date": date.today().isoformat()}


def usage_status(user_id: int) -> dict:
    with connect() as db:
        row = ensure_limit_row(db, user_id)
        today = date.today().isoformat()
        if row["last_reset_date"] != today:
            db.execute("UPDATE usage_limits SET used_today=0, last_reset_date=? WHERE user_id=?", (today, user_id))
            row["used_today"] = 0
            row["last_reset_date"] = today
        return {
            "daily_limit": row["daily_limit"],
            "used_today": row["used_today"],
            "remaining": max(0, row["daily_limit"] - row["used_today"]),
            "last_reset_date": row["last_reset_date"],
        }


def consume_usage(user_id: int, usage_type: str) -> dict:
    with connect() as db:
        row = ensure_limit_row(db, user_id)
        today = date.today().isoformat()
        used = row["used_today"] if row["last_reset_date"] == today else 0
        if row["last_reset_date"] != today:
            db.execute("UPDATE usage_limits SET used_today=0, last_reset_date=? WHERE user_id=?", (today, user_id))
        if used >= row["daily_limit"]:
            raise HTTPException(status_code=429, detail="今日免费调用次数已用完，请联系管理员重置或提高限额")
        db.execute(
            "UPDATE usage_limits SET used_today = used_today + 1, last_reset_date=? WHERE user_id=?",
            (today, user_id),
        )
        db.execute(
            "INSERT INTO usage_logs(user_id, type, created_at) VALUES(?, ?, ?)",
            (user_id, usage_type, now_iso()),
        )
        return {
            "daily_limit": row["daily_limit"],
            "used_today": used + 1,
            "remaining": max(0, row["daily_limit"] - used - 1),
            "last_reset_date": today,
        }
