"""Autentifikatsiya va profil route'lari — kengaytirilgan versiya.

Yangiliklar:
  - register: email tasdiqlash tokeni yaratiladi va email jo'natiladi (background).
  - GET /api/v1/auth/verify-email?token=... — emailni tasdiqlash.

Endpoint'lar:
  POST /api/v1/auth/register        — ro'yxatdan o'tish
  POST /api/v1/auth/login           — kirish (token juftligi)
  POST /api/v1/auth/refresh         — access tokenni yangilash (rotatsiya)
  POST /api/v1/auth/logout          — chiqish
  GET  /api/v1/auth/verify-email    — email tasdiqlash
  POST /api/v1/auth/resend-verification — qayta tasdiqlash emaili
  GET  /api/v1/me                   — profil
  PATCH /api/v1/me                  — profilni yangilash
  POST /api/v1/me/change-password   — parolni o'zgartirish
  DELETE /api/v1/me                 — akkauntni o'chirish
"""

import hashlib
import os
import base64
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import select, update

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.email import send_verification_email
from app.core.security import hash_password, hash_token, verify_password
from app.core.tokens import create_access_token, create_refresh_token, decode_token
from app.db.models import RefreshToken, User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair
from app.schemas.user import ChangePassword, DeleteAccount, UserCreate, UserPublic, UserUpdate

settings = get_settings()

# Ikki router: biri /auth ostida, biri /me ostida.
auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])


def _generate_email_token() -> tuple[str, str]:
    """Email tasdiqlash tokeni hosil qiladi.

    Qaytaradi: (raw_token, token_hash)
    raw_token — emailga yuboriladi (URL'da).
    token_hash — DB'da saqlanadi (xavfsizlik uchun).
    """
    raw = os.urandom(32)
    raw_token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return raw_token, token_hash


async def _store_refresh_token(db: DbSession, user_id: int, raw_token: str) -> None:
    """Refresh tokenni hashlab DB ga yozadi (xom token saqlanmaydi)."""
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
    )


async def _issue_tokens(db: DbSession, user_id: int) -> TokenPair:
    """Yangi access+refresh juftligini yaratadi va refresh'ni saqlaydi."""
    access = create_access_token(user_id)
    refresh = create_refresh_token(user_id)
    await _store_refresh_token(db, user_id, refresh)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_seconds,
    )


# ─── POST /auth/register ──────────────────────────────────────────────────────

@auth_router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> User:
    """Ro'yxatdan o'tish: email+parol, email tasdiqlash emaili yuboriladi."""
    email = payload.email.lower()

    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email allaqachon band")

    raw_token, token_hash = _generate_email_token()

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        is_verified=False,
        email_verification_token_hash=token_hash,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Email jo'natish — background task (asosiy so'rovni bloklamamasin)
    background_tasks.add_task(send_verification_email, email, raw_token)

    return user


# ─── GET /auth/verify-email ───────────────────────────────────────────────────

@auth_router.get("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    token: str = Query(description="Email tasdiqlash tokeni"),
    db: DbSession = None,
) -> dict:
    """Email manzilini tasdiqlash.

    Foydalanuvchi email'dagi havolaga o'tganda shu endpoint chaqiriladi.
    Token SHA-256 bilan hashlanib DB'da qidiriladi.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = await db.scalar(
        select(User).where(User.email_verification_token_hash == token_hash)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token yaroqsiz yoki allaqachon ishlatilgan",
        )
    if user.is_verified:
        return {"message": "Email allaqachon tasdiqlangan"}

    user.is_verified = True
    user.email_verification_token_hash = None  # Token bir marta ishlatiladi
    await db.commit()

    return {"message": "Email muvaffaqiyatli tasdiqlandi! Endi tizimga kirishingiz mumkin."}


# ─── POST /auth/resend-verification ──────────────────────────────────────────

@auth_router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(
    user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> None:
    """Tasdiqlash emailini qayta jo'natish."""
    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email allaqachon tasdiqlangan",
        )

    raw_token, token_hash = _generate_email_token()
    user.email_verification_token_hash = token_hash
    await db.commit()

    background_tasks.add_task(send_verification_email, user.email, raw_token)


# ─── POST /auth/login ─────────────────────────────────────────────────────────

@auth_router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    """Kirish: hisob ma'lumotlarini token juftligiga almashtirish.

    Xavfsizlik: foydalanuvchi topilmasa ham, parol noto'g'ri bo'lsa ham —
    BIR XIL umumiy xato (401). "email topilmadi" deb aytmaymiz.
    """
    email = payload.email.lower()
    user = await db.scalar(select(User).where(User.email == email))

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="email yoki parol noto'g'ri"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="akkaunt faol emas")

    tokens = await _issue_tokens(db, user.id)
    await db.commit()
    return tokens


# ─── POST /auth/refresh ───────────────────────────────────────────────────────

@auth_router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    """Refresh: eski refresh tokenni yangi juftlikka almashtirish (rotatsiya)."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh token yaroqsiz yoki bekor qilingan"
    )

    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc

    token_hash = hash_token(payload.refresh_token)
    stored = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if stored is None or stored.revoked_at is not None:
        raise unauthorized
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise unauthorized

    stored.revoked_at = datetime.now(UTC)
    user_id = int(claims["sub"])
    tokens = await _issue_tokens(db, user_id)
    await db.commit()
    return tokens


# ─── POST /auth/logout ────────────────────────────────────────────────────────

@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: CurrentUser, db: DbSession) -> None:
    """Chiqish: foydalanuvchining barcha faol refresh tokenlarini bekor qiladi."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


# ─── GET /me ──────────────────────────────────────────────────────────────────

@me_router.get("", response_model=UserPublic)
async def get_me(user: CurrentUser) -> User:
    """Profil: joriy foydalanuvchi ma'lumotlari."""
    return user


# ─── PATCH /me ────────────────────────────────────────────────────────────────

@me_router.patch("", response_model=UserPublic)
async def update_me(payload: UserUpdate, user: CurrentUser, db: DbSession) -> User:
    """Profilni yangilash: display_name / email."""
    if payload.email is not None:
        new_email = payload.email.lower()
        if new_email != user.email:
            taken = await db.scalar(select(User).where(User.email == new_email))
            if taken is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email band")
            user.email = new_email
    if payload.display_name is not None:
        user.display_name = payload.display_name

    await db.commit()
    await db.refresh(user)
    return user


# ─── POST /me/change-password ─────────────────────────────────────────────────

@me_router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(payload: ChangePassword, user: CurrentUser, db: DbSession) -> None:
    """Parolni o'zgartirish.

    Joriy parol noto'g'ri bo'lsa 401. Muvaffaqiyatda — barcha refresh tokenlar
    bekor qilinadi (boshqa qurilmalarda qayta login majburlanadi).
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="joriy parol noto'g'ri"
        )

    user.password_hash = hash_password(payload.new_password)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


# ─── DELETE /me ───────────────────────────────────────────────────────────────

@me_router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(payload: DeleteAccount, user: CurrentUser, db: DbSession) -> None:
    """Akkauntni o'chirish: parol bilan tasdiqlash.

    User o'chirilganda ON DELETE CASCADE tufayli uning havolalari va
    refresh tokenlari ham o'chadi.
    """
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="parol noto'g'ri")

    await db.delete(user)
    await db.commit()
