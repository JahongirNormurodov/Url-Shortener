"""API kalit boshqaruvi route'lari.

Endpoint'lar:
  POST   /api/v1/me/api-keys        — yangi kalit yaratish
  GET    /api/v1/me/api-keys        — kalit ro'yxati
  DELETE /api/v1/me/api-keys/{id}   — kalitni o'chirish (revoke)

Xavfsizlik:
  - Kalit yaratilganda raw qiymat faqat BIR MARTA ko'rsatiladi.
  - DB da faqat SHA-256 hash saqlanadi.
  - Format: sk_live_<base64url(32 bayt)>
"""

import base64
import os

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import ApiKey
from app.schemas.api_key import ApiKeyCreate, ApiKeyList, ApiKeyPublic

settings = get_settings()
router = APIRouter(prefix="/me/api-keys", tags=["api-keys"])


def _generate_raw_key() -> str:
    """Kriptografik tasodifiy API kalit hosil qiladi.

    Format: sk_live_<base64url(32 bytes)>
    Uzunlik: ~55 belgi — qisqa, lekin yetarlicha entropiya (256 bit).
    """
    raw_bytes = os.urandom(32)
    key_part = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")
    return f"{settings.api_key_prefix}{key_part}"


@router.post("", response_model=ApiKeyPublic, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    user: CurrentUser,
    db: DbSession,
) -> ApiKeyPublic:
    """Yangi API kalit yaratish.

    Javobdagi `raw_key` — bu kalitning yagona ko'rinish imkoni.
    Iltimos, uni xavfsiz joyga saqlang (password manager va h.k.).
    """
    raw_key = _generate_raw_key()
    key_hash = hash_token(raw_key)

    api_key = ApiKey(
        user_id=user.id,
        key_hash=key_hash,
        name=payload.name,
        is_active=True,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    # raw_key ni javobga qo'shamiz — faqat shu safar!
    result = ApiKeyPublic.model_validate(api_key)
    result.raw_key = raw_key
    return result


@router.get("", response_model=ApiKeyList)
async def list_api_keys(user: CurrentUser, db: DbSession) -> ApiKeyList:
    """Barcha API kalitlar ro'yxati (raw_key ko'rsatilmaydi)."""
    rows = list(
        await db.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == user.id)
            .order_by(ApiKey.created_at.desc())
        )
    )
    total = await db.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id)
    ) or 0

    return ApiKeyList(
        items=[ApiKeyPublic.model_validate(k) for k in rows],
        total=total,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: int, user: CurrentUser, db: DbSession) -> None:
    """API kalitni bekor qilish (revoke).

    Kalitni o'chirilgan emas, faqat `is_active=False` qilinadi — audit uchun yozuv saqlanadi.
    """
    api_key = await db.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kalit topilmadi")

    api_key.is_active = False
    await db.commit()
