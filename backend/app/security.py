"""Telegram WebApp initData tekshiruvi va JWT autentifikatsiya."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException
import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User

MAX_INITDATA_AGE_SECONDS = 24 * 3600
JWT_ALGORITHM = "HS256"


class AuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str, max_age: int = MAX_INITDATA_AGE_SECONDS) -> dict:
    """Telegram rasmiy algoritmi: HMAC-SHA256(key='WebAppData', bot_token) bilan imzo tekshiriladi."""
    if not bot_token:
        raise AuthError("BOT_TOKEN sozlanmagan")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("hash mavjud emas")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("initData imzosi noto'g'ri")
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise AuthError("auth_date noto'g'ri")
    if time.time() - auth_date > max_age:
        raise AuthError("initData eskirgan")
    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        raise AuthError("user ma'lumoti noto'g'ri")
    if "id" not in user:
        raise AuthError("user ma'lumoti yo'q")
    return {"user": user, "start_param": pairs.get("start_param")}


def tg_identity(
    x_telegram_init_data: str | None = Header(default=None),
    x_dev_user: str | None = Header(default=None),
) -> dict:
    """Mini App kirishi. Dev rejimda (DEV_AUTH_BYPASS) X-Dev-User orqali ham kirish mumkin."""
    if settings.dev_bypass_enabled and x_dev_user:
        if not x_dev_user.isdigit():
            raise HTTPException(400, "X-Dev-User raqam bo'lishi kerak")
        return {"user": {"id": int(x_dev_user), "first_name": "Dev", "username": f"dev{x_dev_user}"},
                "start_param": None}
    if not x_telegram_init_data:
        raise HTTPException(401, "Telegram initData yuborilmadi")
    try:
        return verify_init_data(x_telegram_init_data, settings.BOT_TOKEN)
    except AuthError as exc:
        raise HTTPException(401, str(exc))


def create_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    return jwt.encode({"sub": str(user.id), "exp": exp}, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Avtorizatsiya talab qilinadi")
    try:
        payload = jwt.decode(authorization[7:], settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(401, "Token yaroqsiz yoki muddati tugagan")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "Foydalanuvchi topilmadi")
    if user.is_banned:
        raise HTTPException(403, "Hisob bloklangan")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Faqat administrator uchun")
    return user
