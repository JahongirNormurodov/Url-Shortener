"""API kalit sxemalari."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    """POST /me/api-keys tanasi."""

    name: str = Field(min_length=1, max_length=100, description="Kalit nomi (masalan: 'My App')")


class ApiKeyPublic(BaseModel):
    """API kalit javobi — `raw_key` faqat YARATILGANDA bir marta chiqadi."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime
    # raw_key — faqat yaratilganda to'ldiriladi, ro'yxatda None bo'ladi
    raw_key: str | None = Field(
        default=None,
        description="Kalit qiymati — faqat yaratilganda bir marta ko'rsatiladi. Saqlang!"
    )


class ApiKeyList(BaseModel):
    """GET /me/api-keys javobi."""

    items: list[ApiKeyPublic]
    total: int
