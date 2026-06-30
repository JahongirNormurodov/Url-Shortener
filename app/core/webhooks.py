"""Webhook yetkazib berish (delivery) moduli.

Ishlash tartibi:
  1. Havola yaratilganda/o'chirilganda `fire_webhooks()` background task chaqiriladi.
  2. Bu funksiya user ning faol webhook'larini topib, har biriga HTTP POST yuboradi.
  3. Har webhook uchun HMAC-SHA256 imzo hosil qilinadi (`X-Signature` sarlavhasi).

Xavfsizlik:
  - Webhook URL'lar SSRF tekshiruvidan o'tkazilmaydi (foydalanuvchi o'z xizmatiga yo'naltiradi).
    Ammo ichki IP'larga to'g'ridan-to'g'ri bo'lmaydi — bu future work.
  - `secret` DB da ochiq saqlangan (kalit, parol emas). Production'da encrypt qilish mumkin.

Imzo tekshirish (qabul qiluvchi tomon):
  import hmac, hashlib
  expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
  assert request.headers["X-Signature"] == f"sha256={expected}"
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime

import httpx

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def _sign_payload(secret: str, payload_bytes: bytes) -> str:
    """HMAC-SHA256 imzo hosil qiladi. Format: 'sha256=<hex>'."""
    mac = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def fire_webhooks(user_id: int, event: str, data: dict) -> None:
    """Berilgan foydalanuvchining aktiv webhook'lariga hodisani yuboradi.

    `event` — "link.created" | "link.deleted" | "link.updated"
    `data`  — hodisa ma'lumotlari (serializatsiya qilinadi)
    """
    # Import bu yerda — circular import'dan qochish uchun
    from sqlalchemy import select

    from app.db.models import Webhook
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        rows = list(
            await db.scalars(
                select(Webhook).where(
                    Webhook.user_id == user_id,
                    Webhook.is_active.is_(True),
                )
            )
        )

    if not rows:
        return

    payload = {
        "event": event,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "data": data,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    headers_base = {
        "Content-Type": "application/json",
        "User-Agent": f"{settings.app_name}/0.2.0",
        "X-Event": event,
    }

    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        for webhook in rows:
            # Yozilgan hodisalar ro'yxatida bormi?
            subscribed = [e.strip() for e in webhook.events.split(",")]
            if event not in subscribed and "*" not in subscribed:
                continue

            signature = _sign_payload(webhook.secret, payload_bytes)
            headers = {**headers_base, "X-Signature": signature}

            try:
                resp = await client.post(
                    webhook.url,
                    content=payload_bytes,
                    headers=headers,
                )
                logger.info(
                    "[webhook] event=%s url=%s status=%d",
                    event, webhook.url, resp.status_code,
                )
            except Exception as exc:
                logger.warning(
                    "[webhook] Yuborishda xato: event=%s url=%s err=%s",
                    event, webhook.url, exc,
                )
                # Qayta urinish mexanizmi (sodda versiya) — hozircha yo'q.
                # Kelajakda: Celery/ARQ task queue orqali retry.
