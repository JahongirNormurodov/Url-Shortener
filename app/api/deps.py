"""FastAPI dependency'lari (umumiy "qarzga beriladigan" qism).

Dependency injection (DI) — FastAPI ning kuchli tomoni. Funksiya argumentida
`Depends(...)` yozsangiz, FastAPI kerakli qiymatni o'zi tayyorlab beradi
(masalan, DB session yoki joriy foydalanuvchi). Bu kodni qisqa va testlab
bo'ladigan qiladi.

Bu yerda asosiy dependency'lar:
  - get_db           : har so'rovga DB session (db/session.py dan re-export).
  - get_current_user : Authorization sarlavhasidagi JWT ni tekshirib, User qaytaradi.
  - get_api_key_user : X-API-Key sarlavhasidagi kalitni tekshirib, User qaytaradi.
  - get_current_user_flex: JWT yoki API key — biri ishlaydi.
"""

from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.core.tokens import decode_token
from app.db.models import ApiKey, User
from app.db.session import get_db

# HTTPBearer — "Authorization: Bearer <token>" sarlavhasini o'qiydi.
# auto_error=False — sarlavha yo'q bo'lsa FastAPI 403 bermasin; biz 401 beramiz.
_bearer = HTTPBearer(auto_error=False)

# APIKeyHeader — "X-API-Key: sk_live_..." sarlavhasini o'qiydi.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Qisqartma turlari (type alias) — route imzolari toza ko'rinishi uchun.
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Joriy foydalanuvchini access token orqali aniqlaydi.

    Bosqichlar:
      1) Authorization sarlavhasi bormi? (yo'q bo'lsa 401)
      2) Token yaroqlimi (imzo + exp + type=access)? (yo'q bo'lsa 401)
      3) Tokendagi user_id bo'yicha foydalanuvchi DB da bormi va faolmi?

    Spec §13: har so'rovda imzo/exp tekshiriladi; user_id faqat tokendan
    olinadi, mijozdan emas (IDOR oldini olish).
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Yaroqsiz yoki yo'q autentifikatsiya tokeni",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise invalid

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.InvalidTokenError as exc:
        raise invalid from exc

    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        raise invalid
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError) as exc:
        raise invalid from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise invalid

    return user


async def get_api_key_user(
    db: DbSession,
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> User:
    """Joriy foydalanuvchini X-API-Key sarlavhasi orqali aniqlaydi.

    Kalit SHA-256 orqali hashlanib DB'da qidiriladi.
    Topilsa — last_used_at yangilanadi.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Yaroqsiz yoki yo'q API kalit",
        headers={"WWW-Authenticate": "ApiKey"},
    )

    if not api_key:
        raise invalid

    key_hash = hash_token(api_key)
    stored = await db.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
        )
    )
    if stored is None:
        raise invalid

    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise invalid

    # last_used_at yangilanadi (monitoring uchun)
    stored.last_used_at = datetime.now(UTC)
    await db.commit()

    return user


async def get_current_user_flex(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    api_key: Annotated[str | None, Security(_api_key_header)],
) -> User:
    """JWT yoki API key — biri ishlaydi (flexible auth).

    Avval JWT ni tekshiradi; bo'lmasa API key'ni tekshiradi.
    Ikkalasi ham yo'q yoki noto'g'ri bo'lsa 401.
    """
    # Avval JWT
    if credentials is not None:
        try:
            return await get_current_user(db, credentials)
        except HTTPException:
            pass  # JWT ishlamadi — API key sinab ko'ramiz

    # Keyin API key
    if api_key is not None:
        try:
            return await get_api_key_user(db, api_key)
        except HTTPException:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Autentifikatsiya talab qilinadi (Bearer token yoki X-API-Key)",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Joriy foydalanuvchi uchun qisqartmalar — route imzolarida ishlatiladi.
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentUserFlex = Annotated[User, Depends(get_current_user_flex)]
