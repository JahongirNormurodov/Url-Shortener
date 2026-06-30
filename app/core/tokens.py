"""JWT access/refresh tokenlarni yaratish va tekshirish.

JWT (JSON Web Token) — imzolangan, o'zida ma'lumot saqlaydigan token.
Tarkibi: header.payload.signature (base64). Imzo (signature) maxfiy kalit
bilan yaratiladi, shuning uchun foydalanuvchi payloadni o'zgartira olmaydi
(o'zgartirsa imzo mos kelmaydi).

Bizda 2 xil token:
  - ACCESS  (~15 daqiqa): har so'rovda yuboriladi, user_id ni tashiydi.
  - REFRESH (~30 kun): faqat yangi access olish uchun. DB da hashlanib saqlanadi,
    rotatsiya/logout'da bekor qilinadi.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings

settings = get_settings()


def _create_token(subject: str, ttl_seconds: int, token_type: str) -> str:
    """Umumiy token yaratuvchi.

    Standart "claim" lar:
      sub  — subject (kimga tegishli; bizda user_id)
      iat  — issued at (yaratilgan vaqt)
      exp  — expiry (tugash vaqti) — PyJWT buni avtomatik tekshiradi
      jti  — JWT ID (noyob; refresh rotatsiyasida foydali)
      type — bizning maydonimiz: access yoki refresh
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "jti": uuid.uuid4().hex,
        "type": token_type,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    return _create_token(str(user_id), settings.access_token_ttl_seconds, "access")


def create_refresh_token(user_id: int) -> str:
    return _create_token(str(user_id), settings.refresh_token_ttl_seconds, "refresh")


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    """Tokenni dekodlaydi va tekshiradi.

    PyJWT imzo va `exp` ni avtomatik tekshiradi; yaroqsiz bo'lsa istisno tashlaydi.
    Biz qo'shimcha `type` ni ham tekshiramiz (access tokenni refresh o'rniga
    ishlatishga yo'l qo'ymaslik uchun).

    Xatolik bo'lsa jwt.InvalidTokenError (yoki vorisi) tashlanadi —
    chaqiruvchi tomon uni 401 ga aylantiradi.
    """
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"kutilgan token turi: {expected_type}")
    return payload