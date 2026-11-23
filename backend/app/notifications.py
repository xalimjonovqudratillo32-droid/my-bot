"""Telegram xabar yuborish (bot tokeni bo'lmasa jim o'tadi)."""
import logging

import httpx

from .config import settings

log = logging.getLogger("tradex.notify")


def send_telegram_message(chat_id: int, text: str) -> bool:
    if not settings.BOT_TOKEN:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{settings.BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except httpx.HTTPError as exc:
        log.warning("Xabar yuborilmadi (%s): %s", chat_id, exc)
        return False
