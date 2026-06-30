"""Ilova sozlamalari.

pydantic-settings yordamida muhit o'zgaruvchilarini (.env) o'qiymiz.
Afzalligi: har bir sozlama TIPGA ega bo'ladi va ishga tushishda TEKSHIRILADI.
Agar majburiy o'zgaruvchi yo'q bo'lsa yoki tip xato bo'lsa, ilova
"qisqa tutashuv" qiladi (darrov ishlamay xato beradi) — bu yaxshi narsa,
chunki xatoni ishga tushganda emas, eng boshida ushlaymiz.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env faylidan o'qish, katta-kichik harfga e'tibor bermaslik
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Umumiy ---
    app_name: str = "url-shortener"
    environment: str = "development"  # development | production
    base_url: str = "http://localhost:8000"  # qisqa havola prefiksi (sho.rt o'rniga)

    # --- Database (PostgreSQL, async) ---
    # Format: postgresql+asyncpg://user:pass@host:port/dbname
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/urlshortener"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- JWT / Auth ---
    # MUHIM: productionda bu maxfiy kalitni .env orqali beriladi, kodga yozilmaydi!
    jwt_secret: str = Field(default="CHANGE_ME_IN_PROD", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60       # 15 daqiqa
    refresh_token_ttl_seconds: int = 30 * 24 * 3600  # 30 kun

    # --- Short code ---
    short_code_length: int = 7  # base62, 62^7 ≈ 3.5 trillion kod

    # --- URL tekshirish ---
    # SSRF himoyasida domenni DNS orqali IP ga aylantirib tekshiramizmi.
    # Testlarda/offlayn rejimida False qilish mumkin (tarmoqqa chiqmaslik uchun).
    url_resolve_dns: bool = True

    # --- Safe Browsing (Google API v4) ---
    # Kalit bo'lmasa safe_status doim "unknown" qoladi (xavfsiz fallback).
    safe_browsing_api_key: str | None = None
    safe_browsing_timeout_seconds: int = 5

    # --- Rate Limiting ---
    rate_limit_enabled: bool = True
    # Sliding window limitlar (request/period format)
    rate_limit_shorten: str = "20/minute"   # POST /shorten
    rate_limit_redirect: str = "120/minute"  # GET /{code}
    rate_limit_bulk: str = "5/minute"        # POST /bulk-shorten
    rate_limit_auth: str = "10/minute"       # POST /auth/login, /register

    # --- API Keys ---
    # Kalit prefiksi (ko'rinishga chiroyli)
    api_key_prefix: str = "sk_live_"

    # --- Redis Cache ---
    link_cache_ttl_seconds: int = 300  # 5 daqiqa

    # --- Webhooks ---
    webhook_timeout_seconds: int = 5
    webhook_max_retries: int = 3

    # --- Email (SMTP) ---
    smtp_host: str = "localhost"
    smtp_port: int = 1025        # MailHog default porti
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@url-shortener.local"
    smtp_from_name: str = "URL Shortener"
    smtp_tls: bool = False       # STARTTLS (port 587)
    smtp_ssl: bool = False       # implicit TLS (port 465)
    # Email yuborish o'chirilganmi? (testlarda)
    email_enabled: bool = True

    # --- GeoIP (spec §11.3) ---
    # MaxMind GeoLite2-City.mmdb fayl yo'li. Bo'sh bo'lsa — ip-api.com fallback.
    geoip_db_path: str = "app/data/GeoLite2-City.mmdb"

    # --- Analytics: click flusher worker ---
    click_flush_interval_seconds: int = 10
    click_flush_batch_size: int = 500

    # --- Webhook delivery worker ---
    webhook_delivery_interval_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    """Sozlamalarni bir marta yuklab, keshda saqlaymiz (singleton).

    @lru_cache tufayli Settings() faqat bir marta yaratiladi — har safar
    .env ni qayta o'qimaymiz. Keyinroq FastAPI dependency sifatida ishlatamiz.
    """
    return Settings()