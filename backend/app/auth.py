from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import connect, row_to_dict
from .security import decode_token


bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    user_id = decode_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    with connect() as db:
        user = row_to_dict(db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    if not user or not user["is_active"]:
        raise HTTPException(status_code=403, detail="用户不存在或已被禁用")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
