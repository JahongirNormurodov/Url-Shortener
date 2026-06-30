"""Click Flusher Worker — Redis buferidan PostgreSQL ga batch insert (spec §10).

Ishlash tartibi:
  1. Redirect handler har clickda Redis ga ikkita narsa yozadi:
       - click_buffer ro'yxatiga (RPUSH) compact JSON event (link_id, ts, ip, ua, ref)
       - increment_click_buffer(short_code) orqali "clicks:{short_code}" counterni oshiradi
         (bu funksiya app/core/cache.py da allaqachon bor edi)

  2. Bu worker har CLICK_FLUSH_INTERVAL soniyada ishga tushadi:
       a. click_buffer dan barcha eventlarni o'qib oladi (LRANGE + LTRIM, atomik)
       b. Har event uchun: User-Agent parse + GeoIP resolve
       c. click_events jadvaliga BULK INSERT
       d. "clicks:{short_code}" counterlarni links.click_count ga qo'shadi va Redis'dan o'chiradi

  3. Shu tufayli redirect javobi DB yozish tezligiga bog'liq bo'lmaydi (non-blocking).

Ishga tushirish (app/main.py lifespan ichida):
    asyncio.create_task(run_click_flusher())
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Redirect.py bilan bir xil bo'lishi shart bo'lgan bufer kaliti
BUFFER_KEY = "click_buffer"


async def _flush_click_events(db_session, redis_client) -> int:
    """Buferdan eventlarni o'qib, click_events jadvaliga batch yozadi.

    Qaytaradi: yozilgan event soni.
    """
    from app.core.geoip import resolve_geo
    from app.core.useragent import parse_user_agent

    batch_size = settings.click_flush_batch_size

    # Atomik: barcha eventlarni o'qib, ularni buferdan kesib tashlaymiz
    pipe = redis_client.pipeline()
    pipe.lrange(BUFFER_KEY, 0, batch_size - 1)
    pipe.ltrim(BUFFER_KEY, batch_size, -1)
    results = await pipe.execute()

    raw_events = results[0]
    if not raw_events:
        return 0

    rows: list[dict] = []
    for raw in raw_events:
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[flusher] JSON parse xatosi: %s", raw)
            continue

        ua_parsed = parse_user_agent(evt.get("ua"))

        ip = evt.get("ip") or ""
        geo = await resolve_geo(ip) if ip else None

        ts_raw = evt.get("ts")
        try:
            clicked_at = (
                datetime.fromisoformat(ts_raw) if ts_raw else datetime.now(timezone.utc)
            )
        except ValueError:
            clicked_at = datetime.now(timezone.utc)

        rows.append({
            "link_id": evt["link_id"],
            "clicked_at": clicked_at,
            "referrer": evt.get("ref"),
            "user_agent": evt.get("ua"),
            "device_type": ua_parsed.get("device_type"),
            "browser": ua_parsed.get("browser"),
            "os": ua_parsed.get("os"),
            "country": geo.country if geo else None,
            "city": geo.city if geo else None,
            "ip_hash": geo.ip_hash if geo else None,
        })

    if not rows:
        return 0

    insert_sql = text("""
        INSERT INTO click_events
            (link_id, clicked_at, referrer, user_agent,
             device_type, browser, os, country, city, ip_hash)
        VALUES
            (:link_id, :clicked_at, :referrer, :user_agent,
             :device_type, :browser, :os, :country, :city, :ip_hash)
    """)
    await db_session.execute(insert_sql, rows)
    await db_session.commit()

    logger.info("[flusher] %d click event yozildi", len(rows))
    return len(rows)


async def _flush_click_counts(db_session, redis_client) -> None:
    """'clicks:{short_code}' counterlarini links.click_count ga qo'shadi.

    increment_click_buffer() (app/core/cache.py) tomonidan yaratilgan
    kalitlarni o'qib, Link.click_count ga qo'shadi, keyin kalitni o'chiradi.
    """
    from sqlalchemy import select

    from app.db.models import Link

    keys = await redis_client.keys("clicks:*")
    if not keys:
        return

    pipe = redis_client.pipeline()
    for key in keys:
        pipe.getdel(key)
    values = await pipe.execute()

    for key, val in zip(keys, values):
        if not val:
            continue
        try:
            count = int(val)
        except (ValueError, TypeError):
            continue

        # "clicks:{short_code}" -> short_code
        short_code = key.split(":", 1)[1] if ":" in key else key

        link = await db_session.scalar(select(Link).where(Link.short_code == short_code))
        if link:
            link.click_count = (link.click_count or 0) + count

    await db_session.commit()
    logger.debug("[flusher] click_count lar yangilandi")


async def run_click_flusher() -> None:
    """Doimiy ishlaydigan flusher loop.

    app/main.py lifespan ichida asyncio.create_task(run_click_flusher())
    bilan ishga tushiriladi.
    """
    from app.core.cache import get_redis
    from app.db.session import SessionLocal

    interval = settings.click_flush_interval_seconds
    logger.info("[flusher] Click flusher worker ishga tushdi (interval=%ds)", interval)

    while True:
        try:
            redis = get_redis()
            async with SessionLocal() as db:
                await _flush_click_events(db, redis)
                await _flush_click_counts(db, redis)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[flusher] Xato: %s", exc, exc_info=True)

        await asyncio.sleep(interval)
