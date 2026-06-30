"""Autentifikatsiya (token) sxemalari (spec §9.0)."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """POST /auth/login tanasi."""

    email: str
    password: str


class TokenPair(BaseModel):
    """Login/refresh javobida qaytadigan token juftligi.

    expires_in — access token necha soniyada eskirishi (mijoz uchun qulaylik).
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """POST /auth/refresh tanasi."""

    refresh_token: str
