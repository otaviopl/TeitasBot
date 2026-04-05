from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    secret = os.getenv("WEB_JWT_SECRET", "").strip()
    if not secret:
        raise ValueError("WEB_JWT_SECRET environment variable is not configured.")
    return secret


def _get_jwt_expiry_hours() -> int:
    raw = os.getenv("WEB_JWT_EXPIRY_HOURS", "72").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 72


def create_access_token(user_id: str, username: str) -> str:
    secret = _get_jwt_secret()
    expiry = datetime.now(timezone.utc) + timedelta(hours=_get_jwt_expiry_hours())
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expiry,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_token(token: str) -> Optional[dict[str, str]]:
    try:
        secret = _get_jwt_secret()
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
        user_id = payload.get("sub")
        username = payload.get("username")
        if not user_id or not username:
            return None
        return {"user_id": user_id, "username": username}
    except (JWTError, ValueError):
        return None
