"""Webhook obunalari boshqaruvi route'lari.

Endpoint'lar:
  POST   /api/v1/webhooks        — yangi webhook ro'yxatdan o'tkazish
  GET    /api/v1/webhooks        — webhook'lar ro'yxati
  DELETE /api/v1/webhooks/{id}   — webhook'ni o'chirish

Xavfsizlik:
  - Secret yaratilganda avtomatik hosil qilinadi (agar berilmasa).
  - Secret faqat yaratilganda ko'rsatiladi (javobda).
  - Webhook URL'lar SSRF tekshiruvidan o'tkaziladi.
"""

import os
import base64

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.urls import InvalidURLError, validate_url
from app.db.models import Webhook
from app.schemas.webhook import ALLOWED_EVENTS, WebhookCreate, WebhookList, WebhookPublic
from app.core.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _generate_secret() -> str:
    """Webhook imzolash uchun tasodifiy secret hosil qiladi."""
    raw = os.urandom(32)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@router.post("", response_model=WebhookPublic, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreate,
    user: CurrentUser,
    db: DbSession,
) -> WebhookPublic:
    """Yangi webhook ro'yxatdan o'tkazish.

    Secret berilmasa avtomatik yaratiladi — faqat shu javobda ko'rsatiladi.
    """
    # Webhook URL ni tekshiramiz (asosiy SSRF himoya)
    try:
        validated_url = validate_url(payload.url, resolve_dns=settings.url_resolve_dns)
    except InvalidURLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Event'lar tekshiruvi
    for event in payload.events:
        if event not in ALLOWED_EVENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Noma'lum hodisa: {event!r}. Ruxsat etilganlar: {ALLOWED_EVENTS}",
            )

    secret = payload.secret or _generate_secret()
    events_str = ",".join(payload.events)

    webhook = Webhook(
        user_id=user.id,
        url=validated_url,
        secret=secret,
        events=events_str,
        is_active=True,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)

    result = WebhookPublic.model_validate(webhook)
    result.secret = secret  # Faqat yaratilganda ko'rsatiladi
    return result


@router.get("", response_model=WebhookList)
async def list_webhooks(user: CurrentUser, db: DbSession) -> WebhookList:
    """Barcha webhook'lar ro'yxati (secret ko'rsatilmaydi)."""
    rows = list(
        await db.scalars(
            select(Webhook)
            .where(Webhook.user_id == user.id)
            .order_by(Webhook.created_at.desc())
        )
    )
    total = await db.scalar(
        select(func.count()).select_from(Webhook).where(Webhook.user_id == user.id)
    ) or 0

    # Secret yo'q versiyasi
    items = []
    for w in rows:
        pub = WebhookPublic.model_validate(w)
        pub.secret = None  # Ro'yxatda secret ko'rsatilmaydi
        items.append(pub)

    return WebhookList(items=items, total=total)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: int, user: CurrentUser, db: DbSession) -> None:
    """Webhook'ni o'chirish."""
    webhook = await db.scalar(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.user_id == user.id)
    )
    if webhook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webhook topilmadi")

    await db.delete(webhook)
    await db.commit()
