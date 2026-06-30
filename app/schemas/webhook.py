"""Webhook sxemalari."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class WebhookCreate(BaseModel):
    """POST /webhooks tanasi."""

    url: str = Field(min_length=1, description="Webhook yuboriladigan URL (HTTPS tavsiya etiladi)")
    secret: str | None = Field(
        default=None,
        min_length=8,
        max_length=255,
        description="HMAC imzolash kaliti (berilmasa avtomatik yaratiladi)"
    )
    events: list[str] = Field(
        default=["link.created", "link.deleted"],
        description="Obuna bo'lingan hodisalar"
    )


ALLOWED_EVENTS = {"link.created", "link.deleted", "link.updated", "*"}


class WebhookPublic(BaseModel):
    """Webhook javobi."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    events: str  # Vergul bilan ajratilgan string (DB formati)
    is_active: bool
    created_at: datetime
    # Secret faqat yaratilganda ko'rsatiladi
    secret: str | None = None


class WebhookList(BaseModel):
    """GET /webhooks javobi."""

    items: list[WebhookPublic]
    total: int
