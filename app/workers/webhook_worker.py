"""Webhook Delivery Worker — imzolangan yetkazib berish + eksponensial retry (spec §12).

Mavjud `app/core/webhooks.py` dagi `fire_webhooks()` faqat "fire-and-forget"
yuborar edi (xato bo'lsa log yoziladi-yu, qayta urinish yo'q edi). Bu modul
o'sha bo'shliqni to'ldiradi: har yuborish `webhook_deliveries` jadvalida
kuzatiladi va muvaffaqiyatsiz bo'lsa eksponensial backoff bilan qayta uriniladi.

Ishlash tartibi:
  1. Hodisa yuz berganda `enqueue_webhook_delivery()` chaqiriladi
     (links.py, auth.py va h.k. dan, BackgroundTasks orqali):
       - Foydalanuvchining mos `events` ro'yxatiga ega barcha aktiv
         Webhook'lari uchun WebhookDelivery("pending") yozuvi yaratiladi.

  2. Bu worker har `webhook_delivery_interval_seconds` da:
       - "pending" va next_retry_at <= now bo'lgan yozuvlarni topadi
       - HMAC-SHA256 imzo bilan POST yuboradi (core/webhooks.py dagi bilan bir xil format)
       - Muvaffaqiyatli (2xx) → status="delivered"
       - Muvaffaqiyatsiz va attempts < webhook_max_retries → keyingi retry vaqti belgilanadi
       - attempts >= webhook_max_retries → status="failed"

Retry jadvali: 1-urinish → 1 daqiqa, 2-urinish → 5 daqiqa, 3-urinish → 30 daqiqa.

Ishga tushirish (app/main.py lifespan ichida):
    asyncio.create_task(run_webhook_delivery_worker())
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

RETRY_DELAYS_MIN = {1: 1, 2: 5, 3: 30}   # urinish raqami -> kutish (daqiqa)


def _sign_payload(secret: str, payload_bytes: bytes) -> str:
    """HMAC-SHA256 imzo hosil qiladi. Format: 'sha256=<hex>' (core/webhooks.py bilan bir xil)."""
    mac = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def enqueue_webhook_delivery(user_id: int, event_type: str, data: dict) -> None:
    """Foydalanuvchining mos webhook'lariga delivery yozuvlarini yaratadi.

    Chaqirish namunasi (route ichida):
        background_tasks.add_task(enqueue_webhook_delivery, user.id, "link.created", {...})
    """
    from sqlalchemy import select

    from app.db.models import Webhook, WebhookDelivery
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        hooks = list(await db.scalars(
            select(Webhook).where(
                Webhook.user_id == user_id,
                Webhook.is_active.is_(True),
            )
        ))
        if not hooks:
            return

        payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        created = 0
        for hook in hooks:
            subscribed = [e.strip() for e in (hook.events or "").split(",")]
            if event_type not in subscribed and "*" not in subscribed:
                continue

            db.add(WebhookDelivery(
                webhook_id=hook.id,
                delivery_id=str(uuid.uuid4()),
                event_type=event_type,
                payload=payload,
                status="pending",
                attempts=0,
                next_retry_at=None,
            ))
            created += 1

        if created:
            await db.commit()


async def _send_delivery(hook_secret: str, hook_url: str, delivery) -> bool:
    """Bitta delivery'ni HTTP POST orqali yuboradi. Muvaffaqiyatli bo'lsa True."""
    payload_bytes = json.dumps(delivery.payload, separators=(",", ":")).encode("utf-8")
    signature = _sign_payload(hook_secret, payload_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature,
        "X-Delivery-Id": delivery.delivery_id,
        "X-Event": delivery.event_type,
        "User-Agent": f"{settings.app_name}/0.3.0",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
            resp = await client.post(hook_url, content=payload_bytes, headers=headers)

        delivery.last_status_code = resp.status_code
        success = 200 <= resp.status_code < 300
        logger.info(
            "[webhook-worker] delivery_id=%s url=%s status=%d ok=%s",
            delivery.delivery_id, hook_url, resp.status_code, success,
        )
        return success

    except Exception as exc:
        delivery.last_error = str(exc)[:500]
        logger.warning(
            "[webhook-worker] delivery_id=%s url=%s xato=%s",
            delivery.delivery_id, hook_url, exc,
        )
        return False


async def _process_pending_deliveries() -> None:
    """Navbatdagi pending/retry delivery larni yuboradi."""
    from sqlalchemy import or_, select

    from app.db.models import Webhook, WebhookDelivery
    from app.db.session import SessionLocal

    now = datetime.now(timezone.utc)
    max_retries = settings.webhook_max_retries

    async with SessionLocal() as db:
        deliveries = list(await db.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                or_(
                    WebhookDelivery.next_retry_at.is_(None),
                    WebhookDelivery.next_retry_at <= now,
                ),
            )
            .limit(50)
        ))
        if not deliveries:
            return

        logger.debug("[webhook-worker] %d delivery ishlanmoqda", len(deliveries))

        for delivery in deliveries:
            hook = await db.get(Webhook, delivery.webhook_id)
            if not hook or not hook.is_active:
                delivery.status = "failed"
                delivery.last_error = "Webhook o'chirilgan yoki topilmadi"
                continue

            delivery.attempts += 1
            success = await _send_delivery(hook.secret, hook.url, delivery)

            if success:
                delivery.status = "delivered"
            elif delivery.attempts >= max_retries:
                delivery.status = "failed"
                logger.warning(
                    "[webhook-worker] delivery_id=%s max retry(%d) ga yetdi",
                    delivery.delivery_id, max_retries,
                )
            else:
                delay_minutes = RETRY_DELAYS_MIN.get(delivery.attempts, 30)
                delivery.next_retry_at = now + timedelta(minutes=delay_minutes)
                logger.info(
                    "[webhook-worker] delivery_id=%s keyingi urinish %d daqiqadan keyin",
                    delivery.delivery_id, delay_minutes,
                )

        await db.commit()


async def run_webhook_delivery_worker() -> None:
    """Doimiy ishlaydigan webhook delivery worker loop.

    app/main.py lifespan ichida asyncio.create_task(run_webhook_delivery_worker())
    bilan ishga tushiriladi.
    """
    interval = settings.webhook_delivery_interval_seconds
    logger.info("[webhook-worker] Webhook delivery worker ishga tushdi (interval=%ds)", interval)

    while True:
        try:
            await _process_pending_deliveries()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[webhook-worker] Xato: %s", exc, exc_info=True)

        await asyncio.sleep(interval)
