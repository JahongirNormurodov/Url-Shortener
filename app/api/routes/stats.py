"""Statistics & Analytics API endpoints (spec §10).

Barcha endpointlar autentifikatsiya talab qiladi (JWT yoki API key — CurrentUserFlex).
Foydalanuvchi faqat O'Z havolalari statistikasini ko'ra oladi (ownership enforced).

Endpointlar:
  GET /api/v1/links/{code}/stats            — to'liq statistika (§10.1)
  GET /api/v1/links/{code}/stats/timeseries — faqat vaqt seriyasi (§10.2)
  GET /api/v1/links/{code}/clicks           — xom click eventlar (§10.3)
  GET /api/v1/stats/overview                — dashboard (§10.4)
  GET /api/v1/links/{code}/stats/export     — CSV/JSON eksport (§10.5)
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated
import csv
import io
import json

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import Integer, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUserFlex, DbSession
from app.db.models import ClickEvent, Link
from app.db.session import engine
from app.schemas.stats import (
    BrowserStat,
    CityStat,
    ClickEventList,
    ClickEventPublic,
    ClickSummary,
    CountryStat,
    DashboardResponse,
    DeviceStat,
    HourStat,
    LinkStatsResponse,
    PeakDay,
    ReferrerStat,
    TimeSeriesPoint,
    TimeSeriesResponse,
    TopLink,
)

router = APIRouter(tags=["statistics"])

# SQLite'da date_trunc/extract funksiyalari yo'q (faqat PostgreSQL'da bor).
# Testlar/dev SQLite ishlatadi, production Postgres ishlatadi — shuning uchun
# dialect'ga qarab to'g'ri SQL ifoda tanlaymiz.
_IS_SQLITE = engine.dialect.name == "sqlite"

_STRFTIME_FORMATS = {
    "hour": "%Y-%m-%d %H:00:00",
    "day":  "%Y-%m-%d 00:00:00",
    "week": "%Y-%W",   # SQLite'da hafta darajasida aniqlik PG bilan bir xil emas, lekin guruhlash uchun yetarli
}


def _trunc(unit: str, column):
    """date_trunc(unit, column) ning dialect-agnostic versiyasi."""
    if _IS_SQLITE:
        fmt = _STRFTIME_FORMATS.get(unit, _STRFTIME_FORMATS["day"])
        return func.strftime(fmt, column)
    return func.date_trunc(unit, column)


def _extract_hour(column):
    """extract('hour', column) ning dialect-agnostic versiyasi."""
    if _IS_SQLITE:
        # strftime natijasi string ("00".."23") qaytaradi — int ga aylantiramiz
        return func.cast(func.strftime("%H", column), Integer)
    return func.extract("hour", column)


async def _get_owned_link(code: str, user_id: int, db: AsyncSession) -> Link:
    """Kod bo'yicha havolani topadi va egalikni tekshiradi (IDOR oldini olish)."""
    link = await db.scalar(select(Link).where(Link.short_code == code))
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Havola topilmadi")
    if link.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu havola sizga tegishli emas")
    return link


def _parse_date_range(from_: str | None, to_: str | None) -> tuple[datetime, datetime]:
    """from/to query parametrlarini datetime ga aylantiradi. Default: so'nggi 30 kun."""
    now = datetime.now(timezone.utc)
    try:
        dt_to   = datetime.fromisoformat(to_).replace(tzinfo=timezone.utc)   if to_   else now
        dt_from = datetime.fromisoformat(from_).replace(tzinfo=timezone.utc) if from_ else (now - timedelta(days=30))
    except ValueError:
        raise HTTPException(status_code=422, detail="from/to formati noto'g'ri (ISO 8601 kutilmoqda)")
    return dt_from, dt_to


# ─── §10.1 — To'liq statistika ───────────────────────────────────────────────

@router.get("/links/{code}/stats", response_model=LinkStatsResponse)
async def get_link_stats(
    code: str,
    user: CurrentUserFlex,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to_: Annotated[str | None, Query(alias="to")]   = None,
    granularity: Annotated[str, Query()]             = "day",
):
    """Bitta havola uchun to'liq statistika (spec §10.1)."""
    link = await _get_owned_link(code, user.id, db)
    dt_from, dt_to = _parse_date_range(from_, to_)

    base_filter = [
        ClickEvent.link_id    == link.id,
        ClickEvent.clicked_at >= dt_from,
        ClickEvent.clicked_at <= dt_to,
    ]

    total_clicks = await db.scalar(
        select(func.count()).select_from(ClickEvent).where(*base_filter)
    ) or 0

    unique_visitors = await db.scalar(
        select(func.count(func.distinct(ClickEvent.ip_hash))).select_from(ClickEvent).where(*base_filter)
    ) or 0

    days = max((dt_to - dt_from).days, 1)
    avg_per_day = round(total_clicks / days, 2)

    peak_row = await db.execute(
        select(
            _trunc("day", ClickEvent.clicked_at).label("d"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .group_by("d")
        .order_by(text("cnt DESC"))
        .limit(1)
    )
    peak = peak_row.first()
    if peak:
        # PG: peak.d datetime obyekti; SQLite: strftime string qaytaradi.
        peak_date_str = peak.d.date().isoformat() if hasattr(peak.d, "date") else str(peak.d).split(" ")[0]
        peak_day = PeakDay(date=peak_date_str, clicks=peak.cnt)
    else:
        peak_day = None

    summary = ClickSummary(
        total_clicks=total_clicks,
        unique_visitors=unique_visitors,
        avg_clicks_per_day=avg_per_day,
        peak_day=peak_day,
    )

    trunc_unit = granularity if granularity in ("hour", "day", "week") else "day"
    ts_rows = await db.execute(
        select(
            _trunc(trunc_unit, ClickEvent.clicked_at).label("bucket"),
            func.count().label("clicks"),
            func.count(func.distinct(ClickEvent.ip_hash)).label("unique"),
        )
        .where(*base_filter)
        .group_by("bucket")
        .order_by(text("bucket DESC"))
    )
    time_series = [TimeSeriesPoint(t=str(r.bucket), clicks=r.clicks, unique=r.unique) for r in ts_rows]

    ref_rows = await db.execute(
        select(
            func.coalesce(ClickEvent.referrer, "direct").label("ref"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .group_by("ref")
        .order_by(text("cnt DESC"))
        .limit(10)
    )
    top_referrers = [ReferrerStat(referrer=r.ref, clicks=r.cnt) for r in ref_rows]

    country_rows = await db.execute(
        select(ClickEvent.country, func.count().label("cnt"))
        .where(*base_filter, ClickEvent.country.isnot(None))
        .group_by(ClickEvent.country)
        .order_by(text("cnt DESC"))
        .limit(20)
    )
    by_country = [CountryStat(country=r.country, clicks=r.cnt) for r in country_rows]

    city_rows = await db.execute(
        select(ClickEvent.country, ClickEvent.city, func.count().label("cnt"))
        .where(*base_filter, ClickEvent.city.isnot(None))
        .group_by(ClickEvent.country, ClickEvent.city)
        .order_by(text("cnt DESC"))
        .limit(20)
    )
    by_city = [CityStat(country=r.country, city=r.city, clicks=r.cnt) for r in city_rows]

    dev_rows = await db.execute(
        select(
            func.coalesce(ClickEvent.device_type, "unknown").label("dt"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .group_by("dt")
        .order_by(text("cnt DESC"))
    )
    by_device = [DeviceStat(device_type=r.dt, clicks=r.cnt) for r in dev_rows]

    br_rows = await db.execute(
        select(
            func.coalesce(ClickEvent.browser, "Other").label("br"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .group_by("br")
        .order_by(text("cnt DESC"))
        .limit(10)
    )
    by_browser = [BrowserStat(browser=r.br, clicks=r.cnt) for r in br_rows]

    hour_rows = await db.execute(
        select(
            _extract_hour(ClickEvent.clicked_at).label("hr"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .group_by("hr")
        .order_by("hr")
    )
    by_hour = [HourStat(hour=int(r.hr), clicks=r.cnt) for r in hour_rows]

    return LinkStatsResponse(
        code=code,
        range={"from": dt_from.date().isoformat(), "to": dt_to.date().isoformat()},
        summary=summary,
        time_series=time_series,
        top_referrers=top_referrers,
        by_country=by_country,
        by_city=by_city,
        by_device=by_device,
        by_browser=by_browser,
        by_hour=by_hour,
    )


# ─── §10.2 — Vaqt seriyasi ───────────────────────────────────────────────────

@router.get("/links/{code}/stats/timeseries", response_model=TimeSeriesResponse)
async def get_timeseries(
    code: str,
    user: CurrentUserFlex,
    db: DbSession,
    granularity: Annotated[str, Query()] = "day",
    from_: Annotated[str | None, Query(alias="from")] = None,
    to_: Annotated[str | None, Query(alias="to")]   = None,
):
    """Faqat vaqt seriyasi (grafik uchun) (spec §10.2)."""
    link = await _get_owned_link(code, user.id, db)
    dt_from, dt_to = _parse_date_range(from_, to_)
    trunc_unit = granularity if granularity in ("hour", "day", "week") else "day"

    rows = await db.execute(
        select(
            _trunc(trunc_unit, ClickEvent.clicked_at).label("bucket"),
            func.count().label("clicks"),
            func.count(func.distinct(ClickEvent.ip_hash)).label("unique"),
        )
        .where(
            ClickEvent.link_id    == link.id,
            ClickEvent.clicked_at >= dt_from,
            ClickEvent.clicked_at <= dt_to,
        )
        .group_by("bucket")
        .order_by("bucket")
    )
    points = [TimeSeriesPoint(t=str(r.bucket), clicks=r.clicks, unique=r.unique) for r in rows]
    return TimeSeriesResponse(granularity=granularity, points=points)


# ─── §10.3 — Xom click eventlar ─────────────────────────────────────────────

@router.get("/links/{code}/clicks", response_model=ClickEventList)
async def get_raw_clicks(
    code: str,
    user: CurrentUserFlex,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()]     = None,
):
    """So'nggi xom click eventlar (audit/debug uchun) (spec §10.3)."""
    link = await _get_owned_link(code, user.id, db)

    q = select(ClickEvent).where(ClickEvent.link_id == link.id)
    if cursor:
        try:
            q = q.where(ClickEvent.id < int(cursor))
        except ValueError:
            pass

    q = q.order_by(ClickEvent.id.desc()).limit(limit + 1)
    rows = list(await db.scalars(q))

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    return ClickEventList(
        items=[
            ClickEventPublic(
                clicked_at=r.clicked_at,
                country=r.country,
                city=r.city,
                device_type=r.device_type,
                browser=r.browser,
                os=r.os,
                referrer=r.referrer,
            )
            for r in items
        ],
        next_cursor=next_cursor,
    )


# ─── §10.4 — Dashboard (per-user overview) ───────────────────────────────────

@router.get("/stats/overview", response_model=DashboardResponse)
async def get_overview(
    user: CurrentUserFlex,
    db: DbSession,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to_: Annotated[str | None, Query(alias="to")]   = None,
):
    """Foydalanuvchi dashboard uchun umumiy statistika (spec §10.4)."""
    dt_from, dt_to = _parse_date_range(from_, to_)
    link_ids_q = select(Link.id).where(Link.user_id == user.id)

    total_links = await db.scalar(
        select(func.count()).select_from(Link).where(Link.user_id == user.id)
    ) or 0

    active_links = await db.scalar(
        select(func.count()).select_from(Link)
        .where(Link.user_id == user.id, Link.is_active.is_(True))
    ) or 0

    total_clicks = await db.scalar(
        select(func.count()).select_from(ClickEvent)
        .where(
            ClickEvent.link_id.in_(link_ids_q),
            ClickEvent.clicked_at >= dt_from,
            ClickEvent.clicked_at <= dt_to,
        )
    ) or 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    clicks_today = await db.scalar(
        select(func.count()).select_from(ClickEvent)
        .where(
            ClickEvent.link_id.in_(link_ids_q),
            ClickEvent.clicked_at >= today_start,
        )
    ) or 0

    top_rows = await db.execute(
        select(Link.short_code, func.count(ClickEvent.id).label("cnt"))
        .join(ClickEvent, ClickEvent.link_id == Link.id)
        .where(
            Link.user_id == user.id,
            ClickEvent.clicked_at >= dt_from,
            ClickEvent.clicked_at <= dt_to,
        )
        .group_by(Link.short_code)
        .order_by(text("cnt DESC"))
        .limit(5)
    )
    top_links = [TopLink(code=r.short_code, clicks=r.cnt) for r in top_rows]

    daily_rows = await db.execute(
        select(
            _trunc("day", ClickEvent.clicked_at).label("bucket"),
            func.count().label("clicks"),
        )
        .where(
            ClickEvent.link_id.in_(link_ids_q),
            ClickEvent.clicked_at >= dt_from,
            ClickEvent.clicked_at <= dt_to,
        )
        .group_by("bucket")
        .order_by("bucket")
    )
    clicks_by_day = [TimeSeriesPoint(t=str(r.bucket), clicks=r.clicks) for r in daily_rows]

    return DashboardResponse(
        total_links=total_links,
        active_links=active_links,
        total_clicks=total_clicks,
        clicks_today=clicks_today,
        top_links=top_links,
        clicks_by_day=clicks_by_day,
    )


# ─── §10.5 — Export ──────────────────────────────────────────────────────────

@router.get("/links/{code}/stats/export")
async def export_stats(
    code: str,
    user: CurrentUserFlex,
    db: DbSession,
    format: Annotated[str, Query()] = "csv",
    from_: Annotated[str | None, Query(alias="from")] = None,
    to_: Annotated[str | None, Query(alias="to")]   = None,
):
    """Statistikani CSV yoki JSON formatida yuklab olish (spec §10.5)."""
    link = await _get_owned_link(code, user.id, db)
    dt_from, dt_to = _parse_date_range(from_, to_)

    rows = list(await db.scalars(
        select(ClickEvent)
        .where(
            ClickEvent.link_id    == link.id,
            ClickEvent.clicked_at >= dt_from,
            ClickEvent.clicked_at <= dt_to,
        )
        .order_by(ClickEvent.clicked_at.desc())
    ))

    if format == "json":
        data = [
            {
                "clicked_at": r.clicked_at.isoformat(),
                "country": r.country,
                "city": r.city,
                "device_type": r.device_type,
                "browser": r.browser,
                "os": r.os,
                "referrer": r.referrer,
            }
            for r in rows
        ]
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{code}_stats.json"'},
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["clicked_at", "country", "city", "device_type", "browser", "os", "referrer"])
    for r in rows:
        writer.writerow([
            r.clicked_at.isoformat(), r.country, r.city,
            r.device_type, r.browser, r.os, r.referrer,
        ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{code}_stats.csv"'},
    )
