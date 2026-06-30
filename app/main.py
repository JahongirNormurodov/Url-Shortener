"""FastAPI ilovasining kirish nuqtasi (entry point) — kengaytirilgan versiya.

Ishga tushirish:
    uv run uvicorn app.main:app --reload

Hujjatlar:
    http://localhost:8000/docs    (Swagger UI)
    http://localhost:8000/redoc   (ReDoc)

Marshrutlash tartibi MUHIM:
  - /api/v1/...  — barcha API endpoint'lari (prefiks bilan).
  - /health      — sog'liq tekshiruvi.
  - /{code}      — ommaviy redirect. ENG OXIRIDA ulanadi (catch-all).
"""

from contextlib import asynccontextmanager

import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routes import auth, links, redirect
from app.api.routes import api_keys, webhooks
from app.api.routes import stats, routing
from app.core.cache import close_redis, init_redis
from app.core.config import get_settings
from app.core.limiter import limiter
from app.db.models import Base
from app.db.session import engine
from app.workers.click_flusher import run_click_flusher
from app.workers.webhook_worker import run_webhook_delivery_worker

settings = get_settings()

# Background worker'lar (click_flusher, webhook_worker) handle'lari —
# graceful shutdown'da bekor qilish (cancel) uchun saqlanadi.
_background_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan — ilova ishga tushganda va to'xtaganda bajariladigan kod.

    startup:  Redis ulanishi, DB jadvallar (dev'da), limiter tayyor.
    shutdown: Redis yopiladi, DB engine yopiladi.
    """
    # --- startup ---
    print(f"[startup] {settings.app_name} v0.2.0 ishga tushdi ({settings.environment})")

    # Redis ulanishini ochish
    try:
        await init_redis()
        print("[startup] Redis ulandi")
    except Exception as exc:
        print(f"[startup] OGOHLANTIRISH: Redis ulanmadi: {exc}")
        print("[startup] Kesh va rate-limiting ishlamaydi, lekin ilova davom etadi.")

    # Dev qulayligi: jadvallarni avtomatik yaratamiz.
    # Production'da Alembic migratsiyalari ishlatiladi.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Background worker'larni ishga tushirish
    _background_tasks.append(
        asyncio.create_task(run_click_flusher(), name="click_flusher")
    )
    _background_tasks.append(
        asyncio.create_task(run_webhook_delivery_worker(), name="webhook_worker")
    )
    print("[startup] Background worker'lar ishga tushdi (click_flusher, webhook_worker)")

    yield

    # --- shutdown ---
    for task in _background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    print("[shutdown] Background worker'lar to'xtatildi")

    await close_redis()
    await engine.dispose()
    print("[shutdown] resurslar yopildi.")


app = FastAPI(
    title="URL Shortener",
    version="0.3.0",
    description=(
        "FastAPI asosidagi URL qisqartiruvchi xizmat.\n\n"
        "**Xususiyatlar:**\n"
        "- JWT + API key autentifikatsiya\n"
        "- Redis kesh va rate-limiting\n"
        "- Password-protected havolalar\n"
        "- UTM parametrlari\n"
        "- QR kod generatsiyasi\n"
        "- Bulk shorten\n"
        "- Webhooks (imzolangan, retry bilan)\n"
        "- Email tasdiqlash\n"
        "- Google Safe Browsing\n"
        "- Click analytics (Redis buffer → PostgreSQL)\n"
        "- Smart routing (geo / device / A/B)\n"
    ),
    lifespan=lifespan,
)

# Limiter'ni app.state ga bog'laymiz (slowapi talab qiladi)
app.state.limiter = limiter

# Rate limit xato handleri
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# SlowAPI middleware
app.add_middleware(SlowAPIMiddleware)

# CORS (ixtiyoriy — frontend bilan ishlash uchun)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Sog'liqni tekshirish (health check)."""
    from app.core.cache import get_redis
    redis_ok = False
    try:
        redis = get_redis()
        await redis.ping()
        redis_ok = True
    except Exception:
        pass

    return {
        "status": "ok",
        "version": "0.3.0",
        "redis": "ok" if redis_ok else "unavailable",
    }


# ─── API v1 router'lari ───────────────────────────────────────────────────────

# Auth
app.include_router(auth.auth_router, prefix="/api/v1")
app.include_router(auth.me_router, prefix="/api/v1")

# Links
app.include_router(links.router, prefix="/api/v1")

# API keys (/api/v1/me/api-keys)
app.include_router(api_keys.router, prefix="/api/v1")

# Webhooks (/api/v1/webhooks)
app.include_router(webhooks.router, prefix="/api/v1")

# Statistics & Analytics (/api/v1/links/{code}/stats, /api/v1/stats/overview)
app.include_router(stats.router, prefix="/api/v1")

# Smart Routing Rules (/api/v1/links/{code}/rules)
app.include_router(routing.router, prefix="/api/v1")

# ─── Ommaviy redirect (prefiks YO'Q, eng oxirida) ────────────────────────────
app.include_router(redirect.router)
