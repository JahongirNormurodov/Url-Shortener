"""Pytest umumiy sozlamalari (fixtures).

Bu yerda test uchun IZOLYATSIYA qilingan muhit tayyorlaymiz:
  - Postgres o'rniga xotiradagi (in-memory) SQLite — tezkor, izsiz.
  - DNS tekshiruvi o'chirilgan (tarmoqqa chiqmaymiz).
  - get_db dependency'si test sessiyasi bilan almashtiriladi.
  - Har test uchun jadvallar qaytadan yaratiladi (toza holat).

Eslatma: muhit o'zgaruvchilari (env) ilovani import qilishdan OLDIN
o'rnatilishi shart, chunki Settings @lru_cache bilan bir marta o'qiladi.
"""

import os

# --- App import qilinishidan OLDIN env o'rnatamiz ---
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["URL_RESOLVE_DNS"] = "false"
os.environ["JWT_SECRET"] = "test-secret-key-123"
os.environ["BASE_URL"] = "http://testserver"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db
from app.db.models import Base
from app.main import app


@pytest_asyncio.fixture
async def db_engine():
    """Har test uchun yangi in-memory SQLite engine + jadvallar.

    StaticPool kerak: in-memory SQLite har ulanishda yangi bo'sh DB beradi,
    StaticPool esa bitta ulanishni qayta ishlatadi — shunda jadvallar saqlanadi.
    """
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_engine):
    """Test mijozi (AsyncClient) — get_db test sessiyasiga bog'langan."""
    TestSession = async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)

    async def _override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client):
    """Ro'yxatdan o'tib, kirган (Authorization sarlavhasi qo'yilgan) mijoz.

    Ko'p testlar autentifikatsiya talab qiladi — shuni bir joyda tayyorlaymiz.
    """
    await client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "password123", "display_name": "Test"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "password123"},
    )
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
