"""Foydalanuvchi bilan bog'liq Pydantic sxemalari (request/response).

Sxema (schema) — bu so'rov tanasi (request body) yoki javob (response) ning
"shakli". FastAPI ulardan foydalanib:
  - kirayotgan JSON ni avtomatik TEKSHIRADI (validation),
  - chiqayotgan obyektni avtomatik JSON ga aylantiradi,
  - /docs sahifasida hujjat hosil qiladi.

Muhim qoida: DB modeli (SQLAlchemy) bilan API sxemasi (Pydantic) — ALOHIDA.
Bu bizga "nimani saqlash" va "nimani ko'rsatish" ni ajratish imkonini beradi
(masalan, password_hash ni hech qachon javobda qaytarmaymiz).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """POST /auth/register tanasi."""

    email: EmailStr
    # min_length — eng oddiy parol siyosati (spec §13: "min length/complexity").
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)


class UserPublic(BaseModel):
    """Foydalanuvchi haqidagi OMMAVIY ma'lumot (javoblarda).

    password_hash bu yerda YO'Q — uni hech qachon tashqariga chiqarmaymiz.
    """

    # from_attributes=True — Pydantic SQLAlchemy obyektidan (User) maydonlarni
    # o'qiy oladi (user.id, user.email, ...), nafaqat dict dan.
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str | None
    created_at: datetime


class UserUpdate(BaseModel):
    """PATCH /me tanasi — barcha maydonlar ixtiyoriy (qisman yangilash)."""

    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=100)


class ChangePassword(BaseModel):
    """POST /me/change-password tanasi."""

    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccount(BaseModel):
    """DELETE /me tanasi — parol bilan tasdiqlash."""

    password: str
