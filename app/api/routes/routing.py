"""Smart Routing Rules API (spec §11.1).

Endpointlar:
  POST   /api/v1/links/{code}/rules         — yangi qoida qo'shish
  GET    /api/v1/links/{code}/rules         — barcha qoidalarni ko'rish
  DELETE /api/v1/links/{code}/rules/{id}    — qoidani o'chirish
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUserFlex, DbSession
from app.core.config import get_settings
from app.core.urls import InvalidURLError, validate_url
from app.db.models import Link, RoutingRule
from app.schemas.stats import RoutingRuleCreate, RoutingRuleList, RoutingRulePublic

settings = get_settings()
router = APIRouter(tags=["smart-routing"])

VALID_RULE_TYPES = {"geo", "device", "ab"}
VALID_DEVICE_KEYS = {"ios", "android", "desktop", "tablet", "bot"}


def _validate_rule(data: RoutingRuleCreate) -> None:
    """Rule yaratishdan oldin ma'lumotlarni tekshiradi."""
    if data.rule_type not in VALID_RULE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"rule_type {data.rule_type!r} noto'g'ri. Ruxsat etilganlar: {sorted(VALID_RULE_TYPES)}",
        )
    if data.rule_type == "device" and data.match_key not in VALID_DEVICE_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Device match_key {data.match_key!r} noto'g'ri. Ruxsat etilganlar: {sorted(VALID_DEVICE_KEYS)}",
        )
    if data.rule_type == "geo" and not data.match_key:
        raise HTTPException(status_code=422, detail="Geo qoida uchun match_key (masalan 'UZ') talab qilinadi")
    if data.weight < 1 or data.weight > 10000:
        raise HTTPException(status_code=422, detail="weight 1 dan 10000 gacha bo'lishi kerak")


@router.post("/links/{code}/rules", response_model=RoutingRulePublic, status_code=201)
async def create_routing_rule(
    code: str,
    body: RoutingRuleCreate,
    user: CurrentUserFlex,
    db: DbSession,
):
    """Smart link uchun yangi routing qoida qo'shish (spec §11.1)."""
    link = await db.scalar(select(Link).where(Link.short_code == code))
    if not link:
        raise HTTPException(status_code=404, detail="Havola topilmadi")
    if link.user_id != user.id:
        raise HTTPException(status_code=403, detail="Bu havola sizga tegishli emas")

    _validate_rule(body)

    try:
        validated_url = validate_url(body.target_url, resolve_dns=settings.url_resolve_dns)
    except InvalidURLError as exc:
        raise HTTPException(status_code=400, detail=f"Maqsad URL noto'g'ri: {exc}") from exc

    rule = RoutingRule(
        link_id=link.id,
        rule_type=body.rule_type,
        match_key=body.match_key,
        target_url=validated_url,
        weight=body.weight,
        priority=body.priority,
        is_active=True,
    )
    db.add(rule)

    link.is_smart = True
    await db.commit()
    await db.refresh(rule)

    return RoutingRulePublic.model_validate(rule)


@router.get("/links/{code}/rules", response_model=RoutingRuleList)
async def list_routing_rules(
    code: str,
    user: CurrentUserFlex,
    db: DbSession,
):
    """Smart link routing qoidalarini ro'yxati (spec §11.1)."""
    link = await db.scalar(select(Link).where(Link.short_code == code))
    if not link:
        raise HTTPException(status_code=404, detail="Havola topilmadi")
    if link.user_id != user.id:
        raise HTTPException(status_code=403, detail="Bu havola sizga tegishli emas")

    rules = list(await db.scalars(
        select(RoutingRule)
        .where(RoutingRule.link_id == link.id)
        .order_by(RoutingRule.priority)
    ))
    return RoutingRuleList(rules=[RoutingRulePublic.model_validate(r) for r in rules])


@router.delete("/links/{code}/rules/{rule_id}", status_code=204)
async def delete_routing_rule(
    code: str,
    rule_id: int,
    user: CurrentUserFlex,
    db: DbSession,
):
    """Routing qoidasini o'chirish (spec §11.1)."""
    link = await db.scalar(select(Link).where(Link.short_code == code))
    if not link:
        raise HTTPException(status_code=404, detail="Havola topilmadi")
    if link.user_id != user.id:
        raise HTTPException(status_code=403, detail="Bu havola sizga tegishli emas")

    rule = await db.scalar(
        select(RoutingRule).where(RoutingRule.id == rule_id, RoutingRule.link_id == link.id)
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Qoida topilmadi")

    await db.delete(rule)

    remaining = await db.scalar(
        select(RoutingRule).where(RoutingRule.link_id == link.id, RoutingRule.is_active.is_(True))
    )
    if not remaining:
        link.is_smart = False

    await db.commit()
