"""Havola (link) sxemalari — kengaytirilgan versiya.

Yangiliklar:
  - LinkCreate: UTM parametrlari, parol himoyasi
  - LinkPublic: safe_status, has_password, UTM
  - BulkShortenRequest/Response: bir so'rovda ≤50 URL
  - PasswordUnlockRequest: parol bilan himoyalangan havolani ochish
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class UtmParams(BaseModel):
    """UTM kampaniya parametrlari (tracking uchun)."""

    utm_source: str | None = Field(default=None, max_length=255,
                                   description="Manba (masalan: google, newsletter)")
    utm_medium: str | None = Field(default=None, max_length=255,
                                   description="Vosita (masalan: cpc, email)")
    utm_campaign: str | None = Field(default=None, max_length=255,
                                     description="Kampaniya nomi")
    utm_term: str | None = Field(default=None, max_length=255,
                                 description="Qidiruv so'zi (CPC kampaniyalar uchun)")
    utm_content: str | None = Field(default=None, max_length=255,
                                    description="Kontent farqlovchi (A/B test uchun)")


class LinkCreate(BaseModel):
    """POST /shorten tanasi.

    url            — qisqartiriladigan asl manzil (majburiy).
    custom_alias   — ixtiyoriy maxsus kod (F3). Berilsa, kod o'rniga ishlatiladi.
    expires_at     — ixtiyoriy eskirish vaqti (F4).
    password       — havola parol bilan himoyalanishi uchun (ixtiyoriy).
    utm_*          — UTM campaign parametrlari (redirect'da URL'ga qo'shiladi).
    """

    url: str = Field(min_length=1)
    custom_alias: str | None = Field(default=None, min_length=1, max_length=16)
    expires_at: datetime | None = None
    password: str | None = Field(default=None, min_length=1, max_length=128,
                                  description="Havola parol himoyasi (ixtiyoriy)")
    # UTM parametrlari inline
    utm_source: str | None = Field(default=None, max_length=255)
    utm_medium: str | None = Field(default=None, max_length=255)
    utm_campaign: str | None = Field(default=None, max_length=255)
    utm_term: str | None = Field(default=None, max_length=255)
    utm_content: str | None = Field(default=None, max_length=255)


class LinkUpdate(BaseModel):
    """PATCH /links/{code} tanasi — barcha maydonlar ixtiyoriy (qisman)."""

    long_url: str | None = None
    custom_alias: str | None = Field(default=None, min_length=1, max_length=16)
    expires_at: datetime | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=128,
                                  description="Yangi parol (null = parolni olib tashlash)")
    # UTM yangilash
    utm_source: str | None = Field(default=None, max_length=255)
    utm_medium: str | None = Field(default=None, max_length=255)
    utm_campaign: str | None = Field(default=None, max_length=255)
    utm_term: str | None = Field(default=None, max_length=255)
    utm_content: str | None = Field(default=None, max_length=255)


class LinkPublic(BaseModel):
    """Bitta havola haqidagi javob (metadata)."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(validation_alias="short_code")
    long_url: str
    is_active: bool
    click_count: int
    expires_at: datetime | None
    created_at: datetime
    # Xavfsizlik holati
    safe_status: str
    # Parol borligini ko'rsatamiz (hashning o'zini emas!)
    has_password: bool = Field(default=False)
    # UTM parametrlari
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_term: str | None = None
    utm_content: str | None = None

    @classmethod
    def model_validate(cls, obj, **kwargs):
        """Link ob'ektidan has_password ni to'g'ri hisoblash."""
        instance = super().model_validate(obj, **kwargs)
        # ORM ob'ektida password_hash bo'lsa — has_password=True
        if hasattr(obj, "password_hash"):
            instance.has_password = obj.password_hash is not None
        return instance


class ShortenResponse(BaseModel):
    """POST /shorten javobi (spec §9.1).

    short_url — to'liq qisqa havola (base_url + code), masalan
    http://localhost:8000/aZ4kP.
    """

    code: str
    short_url: str
    long_url: str
    expires_at: datetime | None
    created_at: datetime
    has_password: bool = False
    safe_status: str = "unknown"


class LinkList(BaseModel):
    """GET /links javobi (kursorli paginatsiya)."""

    items: list[LinkPublic]
    next_cursor: str | None
    total: int


# ─── Bulk shorten ───────────────────────────────────────────────────────────


class BulkShortenItem(BaseModel):
    """Bulk so'rovdagi bitta URL."""

    url: str = Field(min_length=1)
    custom_alias: str | None = Field(default=None, min_length=1, max_length=16)
    expires_at: datetime | None = None
    utm_source: str | None = Field(default=None, max_length=255)
    utm_medium: str | None = Field(default=None, max_length=255)
    utm_campaign: str | None = Field(default=None, max_length=255)


class BulkShortenRequest(BaseModel):
    """POST /bulk-shorten tanasi — ≤50 ta URL."""

    urls: list[BulkShortenItem] = Field(min_length=1, max_length=50)


class BulkShortenResultItem(BaseModel):
    """Bulk javobdagi bitta natija."""

    url: str          # Kirish URL (reference uchun)
    ok: bool          # Muvaffaqiyatli?
    short_url: str | None = None
    code: str | None = None
    error: str | None = None  # Xato bo'lsa sabab


class BulkShortenResponse(BaseModel):
    """POST /bulk-shorten javobi."""

    results: list[BulkShortenResultItem]
    success_count: int
    error_count: int


# ─── Password unlock ─────────────────────────────────────────────────────────


class PasswordUnlockRequest(BaseModel):
    """Parol bilan himoyalangan havolani ochish.

    GET /{code}?password=<parol> yoki POST /{code}/unlock body sifatida.
    Biz query parametr yolini tanlaymiz — soddaroq.
    Bu sxema faqat hujjatlash uchun (FastAPI docs'da chiqarish uchun).
    """

    password: str = Field(min_length=1, max_length=128)
