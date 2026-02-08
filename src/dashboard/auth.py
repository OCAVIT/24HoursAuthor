"""Авторизация дашборда: JWT + middleware."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
from passlib.hash import bcrypt

from src.config import settings

ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7
COOKIE_NAME = "dashboard_token"


def create_access_token(username: str) -> str:
    """Создать JWT токен."""
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, settings.dashboard_secret_key, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    """Проверить JWT токен, вернуть username или None."""
    try:
        payload = jwt.decode(token, settings.dashboard_secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def verify_password(plain_password: str) -> bool:
    """Проверить пароль против хеша из настроек."""
    if not settings.dashboard_password_hash:
        return False
    return bcrypt.verify(plain_password, settings.dashboard_password_hash)


async def login(request: Request) -> Response:
    """POST /api/dashboard/login — авторизация, выдача JWT cookie."""
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")

    if username != settings.dashboard_username or not verify_password(password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    token = create_access_token(username)
    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=TOKEN_EXPIRE_DAYS * 86400,
        samesite="lax",
        secure=False,  # True в production с HTTPS
    )
    return response


def get_current_user(request: Request) -> str:
    """Dependency: извлечь и проверить JWT из cookie. Возвращает username."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    username = verify_token(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Токен невалиден или истёк")
    return username
