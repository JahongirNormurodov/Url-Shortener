"""Google Safe Browsing API v4 integratsiyasi.

Ishlash tartibi:
  1. Havola yaratilganda/yangilanganda `check_url()` chaqiriladi (background task).
  2. API javobiga qarab `safe_status` yangilanadi: "safe" | "unsafe" | "unknown".
  3. Redirect paytida `safe_status == "unsafe"` bo'lsa 451 qaytariladi.

Fallback: API kaliti yo'q bo'lsa yoki tarmoq xatosi bo'lsa — "unknown" qoladi.
Bu yondashuv spec talabi bilan mos: "safe_status doim 'unknown' bo'lishi mumkin".

API hujjati: https://developers.google.com/safe-browsing/v4/lookup-api
"""

import httpx

from app.core.config import get_settings

settings = get_settings()

# Tekshiriladigan tahdid turlari (Google SB kategoriyalari)
THREAT_TYPES = [
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
]

PLATFORM_TYPES = ["ANY_PLATFORM"]
ENTRY_TYPES = ["URL"]

SAFE_BROWSING_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"


async def check_url_safety(url: str) -> str:
    """URL ni Google Safe Browsing API orqali tekshiradi.

    Qaytaradi:
      "safe"    — API "tahdid yo'q" dedi
      "unsafe"  — API tahdid topdi
      "unknown" — API kaliti yo'q, tarmoq xatosi, yoki timeout

    Xato bo'lsa exception tashlamaydi — faqat "unknown" qaytaradi.
    """
    if not settings.safe_browsing_api_key:
        return "unknown"

    payload = {
        "client": {
            "clientId": settings.app_name,
            "clientVersion": "0.2.0",
        },
        "threatInfo": {
            "threatTypes": THREAT_TYPES,
            "platformTypes": PLATFORM_TYPES,
            "threatEntryTypes": ENTRY_TYPES,
            "threatEntries": [{"url": url}],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.safe_browsing_timeout_seconds) as client:
            response = await client.post(
                SAFE_BROWSING_URL,
                params={"key": settings.safe_browsing_api_key},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        # Javobda "matches" bo'lmasa — xavfsiz
        if not data.get("matches"):
            return "safe"
        else:
            return "unsafe"

    except Exception:
        # Tarmoq xatosi, timeout, yoki boshqa muammo — "unknown" fallback
        return "unknown"


async def update_link_safe_status(link_id: int, url: str) -> None:
    """Background task: havola safe_status'ini yangilaydi.

    Bu funksiya `BackgroundTasks.add_task()` orqali chaqiriladi —
    asosiy so'rovni kechiktirmaydi.
    """
    # Import bu yerda — circular import'dan qochish uchun
    from sqlalchemy import update

    from app.db.models import Link
    from app.db.session import SessionLocal

    status = await check_url_safety(url)

    async with SessionLocal() as db:
        await db.execute(
            update(Link).where(Link.id == link_id).values(safe_status=status)
        )
        await db.commit()
