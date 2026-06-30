"""Database ulanishi (SQLAlchemy 2.0, async).

Tushunchalar:
  - ENGINE: DB ga ulanish "havzasi" (connection pool). Bitta marta yaratiladi.
  - SESSION: bitta so'rov davomidagi "suhbat" (tranzaksiya birligi).
    Har HTTP so'rovga yangi session ochamiz, oxirida yopamiz.
  - get_db(): FastAPI dependency — har so'rovga session beradi va yopadi.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

# Async engine — postgresql+asyncpg drayveri orqali.
# echo=True qo'ysangiz, har bir SQL so'rov logga chiqadi (o'rganishda foydali).
engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,  # "o'lik" ulanishlarni avtomatik tekshirish
)

# Session "fabrikasi" — har chaqirilganda yangi AsyncSession yaratadi.
# expire_on_commit=False: commit'dan keyin obyektlar "eskirmaydi"
# (javobni qaytarayotganda qayta DB so'rovi bo'lmasligi uchun).
SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: har so'rovga DB session beradi.

    `yield` dan oldin — sessionni ochamiz va so'rovga "qarzga beramiz".
    `yield` dan keyin — so'rov tugagach, session avtomatik yopiladi
    (xatolik bo'lsa ham — shuning uchun `async with` ishlatamiz).
    """
    async with SessionLocal() as session:
        yield session