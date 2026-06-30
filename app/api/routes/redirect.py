"""Ommaviy redirect route'i — kengaytirilgan versiya (analytics + smart routing bilan).

GET /{code} — bu YAGONA ommaviy (autentifikatsiyasiz) endpoint.

Hal qilish quvuri (resolution pipeline) — spec §9.2:
  1. Redis keshdan o'qish (cache hit → DB'ga bormaymiz).
  2. Cache miss → DB'dan o'qish, keyin keshga yozish.
  3. Topilmasa → 404.
  4. Eskirgan? fallback_url → 302 fallback, aks holda → 410.
  5. Faol emas → 410.
  6. Xavfsiz emas (safe_status == "unsafe") → 451 (Legal Reasons).
  7. Parol himoyasi: ?password= parametri; to'g'ri bo'lmasa 403.
  8. Smart routing (is_smart=True): geo → device → A/B → default long_url (spec §11).
  9. UTM parametrlari: destination URL'ga qo'shiladi.
  10. Click event Redis buferiga (non-blocking) + 302 yo'naltirish.

NEGA 302 (301 emas)? Spec §9.2: 302 (vaqtinchalik) — brauzer keshlamaydi,
shuning uchun har tashrifda serverimizga keladi (analitika ishlaydi).

YANGILIKLAR (eski versiyaga nisbatan):
  - Click eventlar endi to'g'ridan-to'g'ri DB ga emas, Redis buferiga yoziladi
    (click_flusher worker keyinroq batafsil ma'lumot bilan DB ga yozadi:
    GeoIP, User-Agent parse). Redirect javobi shu sabab tezroq bo'ladi.
  - is_smart=True bo'lgan havolalar uchun geo/device/A-B routing ishlaydi.
"""

import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import ParseResult, parse_qs, urlencode, urlparse, urlunparse

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.cache import get_link_cache, get_redis, increment_click_buffer, set_link_cache
from app.core.config import get_settings
from app.core.geoip import get_real_ip, resolve_geo
from app.core.routing import get_rules_for_link, resolve_smart_url
from app.core.security import verify_password
from app.core.useragent import parse_user_agent
from app.db.models import Link
from app.workers.click_flusher import BUFFER_KEY

settings = get_settings()
router = APIRouter(tags=["redirect"])


def _append_utm(base_url: str, link: "Link | dict") -> str:
    """UTM parametrlarini URL'ga qo'shadi.

    Agar havolada UTM parametrlari sozlangan bo'lsa, ularni destination URL'ga
    qo'shamiz. Mavjud query parametrlar saqlanib qoladi.
    """
    def _get(obj, key):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    utm_params = {}
    for param in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
        val = _get(link, param)
        if val:
            utm_params[param] = val

    if not utm_params:
        return base_url

    parsed: ParseResult = urlparse(base_url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    merged = {k: v[0] if isinstance(v, list) else v for k, v in existing.items()}
    merged.update(utm_params)
    new_query = urlencode(merged)
    new_parsed = parsed._replace(query=new_query)
    return urlunparse(new_parsed)


def _link_to_cache_dict(link: Link) -> dict:
    """ORM Link ob'ektini kesh uchun dict'ga aylantiradi."""
    return {
        "id": link.id,                       # smart routing va click buffer uchun kerak
        "long_url": link.long_url,
        "is_active": link.is_active,
        "is_smart": link.is_smart,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "fallback_url": link.fallback_url,
        "safe_status": link.safe_status,
        "has_password": link.password_hash is not None,
        # password_hash O'ZI keshda saqlanmaydi — verify uchun DB ga boramiz.
        "utm_source": link.utm_source,
        "utm_medium": link.utm_medium,
        "utm_campaign": link.utm_campaign,
        "utm_term": link.utm_term,
        "utm_content": link.utm_content,
        "db_id": link.id,
    }


async def _push_click_event(link_id: int, ip: str, ua: str | None, referrer: str | None) -> None:
    """Click eventni Redis buferiga non-blocking yozadi.

    click_flusher worker (app/workers/click_flusher.py) keyinroq bu buferni
    o'qib, User-Agent va GeoIP ma'lumotlari bilan boyitib, click_events
    jadvaliga batch INSERT qiladi. Redis xatosi redirect'ni bloklamasin
    deb exception yutiladi.
    """
    event = {
        "link_id": link_id,
        "ts": datetime.now(UTC).isoformat(),
        "ip": ip,
        "ua": ua,
        "ref": referrer,
    }
    try:
        redis = get_redis()
        await redis.rpush(BUFFER_KEY, json.dumps(event))
    except Exception:
        pass  # Redis xatosi redirect'ni bloklamamasin


@router.get("/{code}", include_in_schema=False)
async def redirect(
    code: str,
    request: Request,
    db: DbSession,
    password: str | None = Query(default=None, description="Parol himoyasi uchun"),
) -> RedirectResponse:
    """Qisqa kodni asl manzilga yo'naltiradi (302)."""

    # ─── 1. Redis keshdan o'qish ──────────────────────────────────────────────
    cached = await get_link_cache(code)
    db_link: Link | None = None

    if cached is None:
        # ─── 2. Cache miss — DB'dan o'qish ────────────────────────────────────
        db_link = await db.scalar(select(Link).where(Link.short_code == code))
        if db_link is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="havola topilmadi")
        await set_link_cache(code, _link_to_cache_dict(db_link))
        data = _link_to_cache_dict(db_link)
    else:
        data = cached

    # ─── 3. Eskirganligini tekshirish ─────────────────────────────────────────
    if data.get("expires_at") is not None:
        expires_at = datetime.fromisoformat(data["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            fallback = data.get("fallback_url")
            if fallback:
                return RedirectResponse(url=fallback, status_code=status.HTTP_302_FOUND)
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="havola eskirgan")

    # ─── 4. Faolligini tekshirish ─────────────────────────────────────────────
    if not data.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="havola faol emas")

    # ─── 5. Safe Browsing tekshiruvi ─────────────────────────────────────────
    if data.get("safe_status") == "unsafe":
        raise HTTPException(
            status_code=status.HTTP_451_UNAVAILABLE_FOR_LEGAL_REASONS,
            detail="Bu havola xavfli deb belgilangan (Safe Browsing)",
        )

    # ─── 6. Parol himoyasi ────────────────────────────────────────────────────
    if data.get("has_password"):
        if not password:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu havola parol bilan himoyalangan. ?password= parametrini qo'shing.",
            )
        if db_link is None:
            db_link = await db.scalar(select(Link).where(Link.short_code == code))
        if db_link is None or not verify_password(password, db_link.password_hash or ""):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Parol noto'g'ri")

    # ─── 7. Smart routing (spec §11) ──────────────────────────────────────────
    destination = data["long_url"]
    link_id = data.get("id") or data.get("db_id") or (db_link.id if db_link else None)

    if data.get("is_smart") and link_id:
        ua_string = request.headers.get("user-agent")
        ua_parsed = parse_user_agent(ua_string)

        peer_ip = request.client.host if request.client else ""
        real_ip = get_real_ip(dict(request.headers), peer_ip)
        geo = await resolve_geo(real_ip)

        rules = await get_rules_for_link(link_id)
        destination = resolve_smart_url(
            rules=rules,
            default_url=data["long_url"],
            country=geo.country,
            city=geo.city,
            device_type=ua_parsed.get("device_type"),
        )

    # ─── 8. UTM qo'shish ──────────────────────────────────────────────────────
    destination = _append_utm(destination, data)

    # ─── 9. Click hisoblash: Redis bufer (non-blocking) ───────────────────────
    if link_id:
        peer_ip = request.client.host if request.client else ""
        real_ip = get_real_ip(dict(request.headers), peer_ip)
        ua_string = request.headers.get("user-agent")
        referrer = request.headers.get("referer") or request.headers.get("referrer")

        # Eski "issiq" hisoblagich (links.click_count) uchun ham bufer ishlatamiz —
        # increment_click_buffer "clicks:{short_code}" ni oshiradi (cache.py da bor edi).
        await increment_click_buffer(code)

        # To'liq analitik event (GeoIP/UA parse keyinroq flusher'da bo'ladi)
        asyncio.create_task(_push_click_event(link_id, real_ip, ua_string, referrer))

    return RedirectResponse(url=destination, status_code=status.HTTP_302_FOUND)
