"""Redis kesh — redirect yo'lida DB yukini kamaytirish.

Ishlash tamoyili:
  - Redirect so'rovi kelganda avval Redis dan o'qiymiz (O(1)).
  - Topilmasa — DB ga boramiz va natijani Redisga yozamiz (set).
  - Havola o'zgarganda yoki o'chirilganda kesh tozalanadi (DEL).

Kalit formati: "link:{short_code}"
TTL: settings.link_cache_ttl_seconds (standart 300 s = 5 daqiqa)

Xavfsizlik: Redis'da faqat OMMAVIY ma'lumot saqlanadi (long_url, expires_at,
is_active, password_hash_exists, safe_status). password_hash O'ZI keshda
saqlanmaydi — faqat "parol bormi?" (bool) ma'lumoti.
"""

import json
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

# Global Redis klient — lifespan'da ishga tushiriladi va yopiladi.
# Oddiy modul darajasida o'zgaruvchi; FastAPI app.state orqali ham bo'lardi,
# lekin bu yondashuv oddiyroq va testlarda ham almashtirish oson.
_redis_client: aioredis.Redis | None = None


async def init_redis() -> None:
    """Redis ulanishini ochadi (lifespan startup da chaqiriladi)."""
    global _redis_client
    _redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    # Ulanishni tekshirish
    await _redis_client.ping()


async def close_redis() -> None:
    """Redis ulanishini yopadi (lifespan shutdown da chaqiriladi)."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def get_redis() -> aioredis.Redis:
    """Redis klientini qaytaradi. `init_redis()` chaqirilmagan bo'lsa — xato."""
    if _redis_client is None:
        raise RuntimeError("Redis ulanishi ishga tushirilmagan. init_redis() chaqiring.")
    return _redis_client


def _cache_key(short_code: str) -> str:
    """Kalit nomini standartlashtiradi."""
    return f"link:{short_code}"


async def get_link_cache(short_code: str) -> dict[str, Any] | None:
    """Keshdan havola ma'lumotlarini o'qiydi.

    Topilsa — dict qaytaradi.
    Topilmasa — None qaytaradi (DB ga borish signali).
    Redis mavjud bo'lmasa — None (fallback, DB dan o'qish davom etadi).
    """
    try:
        redis = get_redis()
        raw = await redis.get(_cache_key(short_code))
        if raw is None:
            return None
        data = json.loads(raw)
        return data
    except Exception:
        # Redis xatosi redirect'ni bloklamamasin — DB fallback.
        return None


async def set_link_cache(short_code: str, data: dict[str, Any]) -> None:
    """Havola ma'lumotlarini keshga yozadi.

    `data` — serializatsiya qilinishi mumkin bo'lgan dict
    (datetime'lar ISO string sifatida).
    TTL: settings.link_cache_ttl_seconds
    """
    try:
        redis = get_redis()
        serialized = json.dumps(data, default=_json_default)
        await redis.setex(
            _cache_key(short_code),
            settings.link_cache_ttl_seconds,
            serialized,
        )
    except Exception:
        pass  # Keshga yozish muvaffaqiyatsiz bo'lsa — davom etamiz.


async def delete_link_cache(short_code: str) -> None:
    """Havola keshini o'chiradi (update/delete operatsiyalarida)."""
    try:
        redis = get_redis()
        await redis.delete(_cache_key(short_code))
    except Exception:
        pass


async def increment_click_buffer(short_code: str) -> int:
    """Redis'da click hisoblagichini oshiradi (bufer).

    Kalit: "clicks:{short_code}"
    Bu yerda faqat INCR — DB ga har clickda yozmaslik uchun.
    Alohida background task (yoki cron) bu buferni DB ga ko'chirishi mumkin.
    Hozircha MVP uchun redirect.py'da ham to'g'ridan-to'g'ri DB yangilanadi,
    lekin ushbu funksiya kelajak uchun tayyor.
    """
    try:
        redis = get_redis()
        count = await redis.incr(f"clicks:{short_code}")
        # 1 soatlik TTL — shu vaqt ichida DB ga ko'chirilishi kutiladi.
        await redis.expire(f"clicks:{short_code}", 3600)
        return int(count)
    except Exception:
        return 0


def _json_default(obj: Any) -> Any:
    """JSON serializatsiya uchun datetime va boshqa turlarni convert qiladi."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Serializatsiya qilib bo'lmaydi: {type(obj)}")
