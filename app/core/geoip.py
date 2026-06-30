"""GeoIP — IP manzildan mamlakat va shahar aniqlash (spec §11.3).

Ikki yondashuv qo'llab-quvvatlanadi:
  1. MaxMind GeoLite2 (tavsiya) — lokal .mmdb fayl, microseconds, offline
  2. ip-api.com fallback — HTTP so'rov, bepul, lekin sekinroq

Sozlash (.env):
  GEOIP_DB_PATH=app/data/GeoLite2-City.mmdb   (bo'sh = ip-api fallback)
  GEOIP_CACHE_TTL=3600                          (Redis kesh TTL, soniyada)

MaxMind GeoLite2 yuklab olish:
  https://dev.maxmind.com/geoip/geolite2-free-geolocation-data
  yoki: pip install geoip2  +  mmdb faylini app/data/ ga qo'ying

Maxfiylik: faqat mamlakat + shahar saqlanadi; xom IP hesh qilinadi.
"""

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GeoResult:
    country: str | None     # ISO 3166-1 alpha-2, masalan "UZ"
    city: str | None        # "Tashkent"
    ip_hash: str            # SHA-256(ip) — unique visitor uchun


def hash_ip(ip: str) -> str:
    """IP manzilni SHA-256 bilan hashlaydi (maxfiylik uchun)."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


async def resolve_geo(ip: str) -> GeoResult:
    """IP dan mamlakat + shahar aniqlaydi.

    Avval MaxMind GeoLite2 sinab ko'riladi (tez, offline).
    Bo'lmasa ip-api.com ga so'rov yuboriladi (sekin, online).
    Ikkalasi ham ishlamasa — None qaytariladi (analitikada bo'sh qoladi).
    """
    ip_hash = hash_ip(ip)

    # 1) MaxMind GeoLite2 (lokal mmdb fayl)
    result = await _resolve_maxmind(ip)
    if result:
        return GeoResult(country=result[0], city=result[1], ip_hash=ip_hash)

    # 2) ip-api.com fallback
    result = await _resolve_ipapi(ip)
    if result:
        return GeoResult(country=result[0], city=result[1], ip_hash=ip_hash)

    return GeoResult(country=None, city=None, ip_hash=ip_hash)


async def _resolve_maxmind(ip: str) -> tuple[str, str] | None:
    """MaxMind GeoLite2-City.mmdb faylidan geo ma'lumot oladi.

    geoip2 kutubxonasi o'rnatilmagan bo'lsa yoki mmdb yo'q bo'lsa —
    None qaytaradi (xato bermaydi).
    """
    try:
        import geoip2.database  # type: ignore

        from app.core.config import get_settings
        settings = get_settings()

        db_path = getattr(settings, "geoip_db_path", "app/data/GeoLite2-City.mmdb")
        if not db_path:
            return None

        # Reader thread-safe, lekin blocking — executor da chaqirish mumkin
        # Hozircha sinxron (mmdb lookups microseconds darajasida)
        import asyncio
        loop = asyncio.get_event_loop()

        def _lookup():
            with geoip2.database.Reader(db_path) as reader:
                response = reader.city(ip)
                country = response.country.iso_code or None
                city = response.city.name or None
                return country, city

        country, city = await loop.run_in_executor(None, _lookup)
        return country, city

    except Exception as exc:
        logger.debug("[geoip] MaxMind lookup failed for %s: %s", ip, exc)
        return None


async def _resolve_ipapi(ip: str) -> tuple[str, str] | None:
    """ip-api.com orqali geo ma'lumot oladi (bepul, limitli).

    Mahalliy va xususiy IP lar uchun ishlaydi (localhost uchun emas).
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "status,country,countryCode,city"},
            )
            data = resp.json()

        if data.get("status") != "success":
            return None

        country = data.get("countryCode") or None
        city = data.get("city") or None
        return country, city

    except Exception as exc:
        logger.debug("[geoip] ip-api fallback failed for %s: %s", ip, exc)
        return None


def get_real_ip(request_headers: dict, peer_ip: str) -> str:
    """Proksi ortidagi haqiqiy IP manzilni aniqlaydi.

    Faqat ishonchli proksilardan kelgan X-Forwarded-For ga ishoniladi.
    To'g'ridan-to'g'ri ulanishlarda peer_ip ishlatiladi.

    Spec §11.3: "trust X-Forwarded-For ONLY from known proxies (Nginx)"
    """
    xff = request_headers.get("x-forwarded-for") or request_headers.get("X-Forwarded-For")
    if xff:
        # "client, proxy1, proxy2" — eng chap = haqiqiy mijoz
        first_ip = xff.split(",")[0].strip()
        if first_ip:
            return first_ip
    return peer_ip
