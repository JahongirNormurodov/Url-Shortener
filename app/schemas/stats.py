"""Analytics / statistika sxemalari (spec §10).

Stats endpointlar uchun Pydantic javob modellari.
"""

from datetime import datetime

from pydantic import BaseModel


# ─── Yordamchi kichik modellar ────────────────────────────────────────────────

class TimeSeriesPoint(BaseModel):
    t: str          # "2026-06-10" yoki "2026-06-10T13:00:00"
    clicks: int
    unique: int = 0


class ReferrerStat(BaseModel):
    referrer: str
    clicks: int


class CountryStat(BaseModel):
    country: str
    clicks: int


class CityStat(BaseModel):
    country: str
    city: str
    clicks: int


class DeviceStat(BaseModel):
    device_type: str
    clicks: int


class BrowserStat(BaseModel):
    browser: str
    clicks: int


class HourStat(BaseModel):
    hour: int       # 0–23
    clicks: int


class PeakDay(BaseModel):
    date: str
    clicks: int


class ClickSummary(BaseModel):
    total_clicks: int
    unique_visitors: int
    avg_clicks_per_day: float
    peak_day: PeakDay | None


# ─── Asosiy javob modellari ───────────────────────────────────────────────────

class LinkStatsResponse(BaseModel):
    """GET /api/v1/links/{code}/stats javobi (spec §10.1)."""

    code: str
    range: dict                         # {"from": "...", "to": "..."}
    summary: ClickSummary
    time_series: list[TimeSeriesPoint]
    top_referrers: list[ReferrerStat]
    by_country: list[CountryStat]
    by_city: list[CityStat]
    by_device: list[DeviceStat]
    by_browser: list[BrowserStat]
    by_hour: list[HourStat]


class TimeSeriesResponse(BaseModel):
    """GET /api/v1/links/{code}/stats/timeseries javobi (spec §10.2)."""

    granularity: str                    # "hour" | "day" | "week"
    points: list[TimeSeriesPoint]


class ClickEventPublic(BaseModel):
    """GET /api/v1/links/{code}/clicks javobi — bitta click (spec §10.3)."""

    clicked_at: datetime
    country: str | None
    city: str | None
    device_type: str | None
    browser: str | None
    os: str | None
    referrer: str | None


class ClickEventList(BaseModel):
    items: list[ClickEventPublic]
    next_cursor: str | None


class TopLink(BaseModel):
    code: str
    clicks: int


class DashboardResponse(BaseModel):
    """GET /api/v1/stats/overview javobi (spec §10.4)."""

    total_links: int
    active_links: int
    total_clicks: int
    clicks_today: int
    top_links: list[TopLink]
    clicks_by_day: list[TimeSeriesPoint]


# ─── Routing rule sxemalari ──────────────────────────────────────────────────

class RoutingRuleCreate(BaseModel):
    """POST /api/v1/links/{code}/rules tanasi (spec §11.1)."""

    rule_type: str          # 'geo' | 'device' | 'ab'
    match_key: str | None = None
    target_url: str
    weight: int = 100
    priority: int = 0


class RoutingRulePublic(BaseModel):
    """Routing rule javobi."""

    model_config = {"from_attributes": True}

    id: int
    rule_type: str
    match_key: str | None
    target_url: str
    weight: int
    priority: int
    is_active: bool


class RoutingRuleList(BaseModel):
    rules: list[RoutingRulePublic]
