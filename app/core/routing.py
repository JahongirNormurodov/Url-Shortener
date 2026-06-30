"""Smart Routing Engine — geo / device / A/B qoidalar asosida URL tanlash (spec §11).

Ishlash tartibi (spec §9.2 resolution pipeline, 5-qadam):
  1. links.is_smart = True bo'lsa bu modul chaqiriladi
  2. routing_rules jadvali priority bo'yicha o'qiladi (kichik = birinchi)
  3. Tartib: geo match → device match → weighted A/B pick → default long_url

Geo match_key formatlari:
  "UZ"           → butun mamlakat uchun
  "UZ:Tashkent"  → shahar darajasida (mamlakat:shahar)

Device match_key:
  "ios" | "android" | "desktop" | "tablet" | "bot"

A/B:
  match_key = None, weight = og'irlik (masalan 50+50=100)
  Tasodifiy tanlash: weight ga proporsional
  Cookie sticky: qaytuvchi foydalanuvchi bir xil variantni oladi (ixtiyoriy)

Kesh: routing qoidalari redis da link payload bilan birga saqlanadi.
"""

import logging
import random

logger = logging.getLogger(__name__)


def _match_geo(match_key: str, country: str | None, city: str | None) -> bool:
    """Geo qoidasi berilgan mamlakat/shahar bilan mos keladimi?"""
    if not country:
        return False
    if ":" in match_key:
        # Shahar darajasi: "UZ:Tashkent"
        key_country, key_city = match_key.split(":", 1)
        return (
            key_country.upper() == country.upper()
            and key_city.lower() == (city or "").lower()
        )
    # Faqat mamlakat: "UZ"
    return match_key.upper() == country.upper()


def _match_device(match_key: str, device_type: str | None) -> bool:
    """Device qoidasi berilgan device_type bilan mos keladimi?"""
    if not device_type:
        return False
    return match_key.lower() == device_type.lower()


def _weighted_pick(ab_rules: list) -> str | None:
    """Og'irlikli tasodifiy tanlash (A/B rotator).

    Har qoida: {"target_url": ..., "weight": 50}
    Jami og'irlik 100 bo'lishi shart emas — nisbiy bo'ladi.
    """
    if not ab_rules:
        return None

    total = sum(r.weight for r in ab_rules)
    if total <= 0:
        return ab_rules[0].target_url

    pick = random.randint(1, total)
    cumulative = 0
    for rule in ab_rules:
        cumulative += rule.weight
        if pick <= cumulative:
            return rule.target_url

    return ab_rules[-1].target_url


def resolve_smart_url(
    rules: list,
    default_url: str,
    country: str | None = None,
    city: str | None = None,
    device_type: str | None = None,
) -> str:
    """Smart link uchun maqsad URL ni aniqlaydi.

    Args:
        rules       : RoutingRule ob'ektlari ro'yxati (priority bo'yicha tartiblangan)
        default_url : Hech qaysi qoida mos kelmasa ishlatiladigan URL
        country     : Foydalanuvchi mamlakatining ISO kodi (masalan "UZ")
        city        : Foydalanuvchi shahri (masalan "Tashkent")
        device_type : "mobile" | "desktop" | "tablet" | "ios" | "android" | "bot"

    Returns:
        Maqsad URL (string)
    """
    # Faqat aktiv qoidalar
    active_rules = [r for r in rules if r.is_active]

    # Tartib: geo > device > ab
    geo_rules    = [r for r in active_rules if r.rule_type == "geo"]
    device_rules = [r for r in active_rules if r.rule_type == "device"]
    ab_rules     = [r for r in active_rules if r.rule_type == "ab"]

    # 1) Geo match (priority bo'yicha tartiblangan, birinchi mos kelgani g'alaba qiladi)
    geo_rules.sort(key=lambda r: r.priority)
    for rule in geo_rules:
        if _match_geo(rule.match_key or "", country, city):
            logger.debug("[routing] geo match: %s → %s", rule.match_key, rule.target_url)
            return rule.target_url

    # 2) Device match
    device_rules.sort(key=lambda r: r.priority)
    for rule in device_rules:
        if _match_device(rule.match_key or "", device_type):
            logger.debug("[routing] device match: %s → %s", rule.match_key, rule.target_url)
            return rule.target_url

    # 3) A/B weighted pick
    if ab_rules:
        picked = _weighted_pick(ab_rules)
        if picked:
            logger.debug("[routing] a/b pick → %s", picked)
            return picked

    # 4) Default
    logger.debug("[routing] fallback → %s", default_url)
    return default_url


async def get_rules_for_link(link_id: int) -> list:
    """DB dan berilgan link uchun barcha aktiv routing qoidalarini oladi.

    Natija priority bo'yicha tartiblangan.
    """
    from sqlalchemy import select

    from app.db.models import RoutingRule
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        rules = list(await db.scalars(
            select(RoutingRule)
            .where(
                RoutingRule.link_id == link_id,
                RoutingRule.is_active.is_(True),
            )
            .order_by(RoutingRule.priority)
        ))
    return rules
