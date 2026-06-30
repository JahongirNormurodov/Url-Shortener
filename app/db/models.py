"""Database modellari (SQLAlchemy 2.0 "typed" uslubi).

Har bir klass — DB dagi bitta jadval. `Mapped[...]` va `mapped_column(...)`
yangi 2.0 uslubi bo'lib, tip ko'rsatkichlari (type hints) bilan birga ishlaydi —
mypy/pyright xatolarni oldindan topadi.

Modellar (jadvалlar):
  - User           — foydalanuvchi
  - RefreshToken   — JWT refresh tokenlari
  - Link           — qisqa havolalar (UTM, password_hash, safe_status)
  - ApiKey         — API kalitlari (dasturiy kirish uchun)
  - Webhook        — webhook obunalari
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Barcha modellar uchun umumiy "ota" klass.

    Alembic migratsiyalari shu Base.metadata orqali jadvallarni biladi.
    """


# Primary key turi: Postgres'da BIGINT (BIGSERIAL), lekin SQLite'da BIGINT
# auto-increment QILMAYDI — faqat INTEGER qiladi. Shuning uchun "variant"
# ishlatamiz: SQLite -> INTEGER, qolganlar -> BigInteger. Bu testlarni
# (in-memory SQLite) ham, productionни (Postgres) ham qo'llab-quvvatlaydi.
PK = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    # Email noyob (unique) va kichik harfga keltirib saqlanadi (app logikasida).
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Email tasdiqlash holati (F: email verification)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Email tasdiqlash tokeni — SHA-256 hash (xom token emailga yuboriladi)
    email_verification_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ORM bog'lanish: user.refresh_tokens orqali tokenlarga kirish.
    # cascade — user o'chirilsa, tokenlari ham o'chadi.
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    # user.links orqali foydalanuvchining havolalariga kirish.
    links: Mapped[list["Link"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    # API kalitlari
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    # Webhook obunalari
    webhooks: Mapped[list["Webhook"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Tokenning O'ZINI emas, SHA-256 hashini saqlaymiz (DB sizib chiqsa ham xavfsiz).
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # revoked_at NULL bo'lsa — token "tirik". logout/rotatsiyada sana qo'yiladi.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class Link(Base):
    """Qisqartirilgan havola.

    Asosiy g'oya: `id` — BIGSERIAL hisoblagich. Yangi havola yaratilganda
    avval qatorni yozamiz, DB bizga `id` beradi, so'ng `id_to_code(id)`
    orqali qisqa kod hosil qilamiz (base62 + aralashtirish).
    """

    __tablename__ = "links"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    # base62 kod yoki foydalanuvchi bergan alias. Noyob va indekslangan.
    short_code: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, nullable=False
    )
    long_url: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # is_custom — kod foydalanuvchi tomonidan berilganmi (alias) yoki avtomatikmi.
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # is_active — soft delete / deaktivatsiya uchun.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # --- Kengaytirilgan xususiyatlar ---
    is_smart: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Password himoyasi: argon2 hash (NULL = parolsiz)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fallback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Safe Browsing holati: "unknown" | "safe" | "unsafe"
    safe_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    # click_count — "issiq" hisoblagich (denormalizatsiya): har redirectda oshadi.
    click_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # expires_at NULL bo'lsa — hech qachon eskirmaydi.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # --- UTM parametrlari (campaign tracking) ---
    utm_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="links")

    # --- Analytics & smart routing (qo'shimcha) ---
    click_events: Mapped[list["ClickEvent"]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )
    routing_rules: Mapped[list["RoutingRule"]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    """API kalitlari — JWT o'rniga statik kalit bilan dasturiy kirish.

    Kalit o'zi (raw) faqat yaratilganda bir marta ko'rsatiladi.
    DB da SHA-256 hash saqlanadi — xuddi refresh token kabi.
    Format: sk_live_<base64url(32 bayt)>
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Kalitning SHA-256 hashi (xom kalit DB da saqlanmaydi)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # Foydalanuvchi bergan nom (masalan: "My App", "CI/CD")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Oxirgi ishlatilgan vaqt (monitoring uchun)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="api_keys")


class Webhook(Base):
    """Webhook obunasi — havola hodisalarida tashqi URL ga POST yuborish.

    Xavfsizlik: har yuborishda `X-Signature: sha256=<hmac>` sarlavhasi qo'shiladi.
    Qabul qiluvchi tomon HMAC'ni `secret` bilan tekshiradi.
    """

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Webhook so'rov yuboriladigan URL
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # HMAC imzolash uchun maxfiy kalit (foydalanuvchi beradi yoki avtomatik)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    # Obuna bo'lgan hodisalar — vergul bilan ajratilgan: "link.created,link.deleted"
    events: Mapped[str] = mapped_column(String(500), nullable=False, default="link.created,link.deleted")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="webhooks")
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )


# ─── ClickEvent ────────────────────────────────────────────────────────────
# Har bir redirect uchun analitik yozuv (spec §10).
# Yozish tartibi: Redirect → Redis RPUSH click_buffer → click_flusher worker
#                 → bu jadval (batch INSERT). Redirect yo'li hech qachon
#                 to'g'ridan-to'g'ri bu jadvalga yozish bilan bloklanmaydi.


class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    # mobile | desktop | tablet | bot
    device_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(32), nullable=True)
    os: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ISO 3166-1 alpha-2 (masalan "UZ")
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Xom IP saqlanmaydi (maxfiylik) — SHA-256 hash unique-visitor hisoblash uchun.
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    link: Mapped["Link"] = relationship(back_populates="click_events")


# ─── RoutingRule ───────────────────────────────────────────────────────────
# Smart link routing qoidalari: geo / device / A/B (spec §11).
# Tartib: geo match → device match → weighted A/B pick → default long_url.
# priority kichik bo'lsa birinchi tekshiriladi.


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("links.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 'geo' | 'device' | 'ab'
    rule_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # geo: 'UZ' yoki 'UZ:Tashkent' | device: 'ios'|'android'|'desktop' | ab: None
    match_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    # A/B uchun og'irlik (masalan 50 = nisbiy og'irlik)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    link: Mapped["Link"] = relationship(back_populates="routing_rules")


# ─── WebhookDelivery ───────────────────────────────────────────────────────
# Webhook yetkazib berish jurnali + retry holati (spec §12).
# Worker "pending" va retry vaqti kelgan yozuvlarni qayta urinadi.
# Eksponensial backoff: 1-urinish → 1 daq, 2-urinish → 5 daq, 3-urinish → 30 daq.


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(PK, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Noyob delivery ID — idempotency uchun X-Delivery-Id sarlavhasiga qo'yiladi
    delivery_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    # 'pending' | 'delivered' | 'failed'
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    webhook: Mapped["Webhook"] = relationship(back_populates="deliveries")