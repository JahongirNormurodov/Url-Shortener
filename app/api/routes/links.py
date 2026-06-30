"""Havolalarni boshqarish route'lari — kengaytirilgan versiya.

Endpoint'lar (hammasi autentifikatsiya talab qiladi):
  POST   /api/v1/shorten          — yangi qisqa havola (UTM, password, safe browsing)
  GET    /api/v1/links            — mening havolalarim (kursorli paginatsiya)
  GET    /api/v1/links/{code}     — bitta havola metadatasi
  PATCH  /api/v1/links/{code}     — havolani yangilash (+ kesh tozalash)
  DELETE /api/v1/links/{code}     — o'chirish (+ kesh tozalash, webhook)
  GET    /api/v1/links/{code}/qr  — QR kod PNG
  POST   /api/v1/bulk-shorten     — bir so'rovda ≤50 ta URL
"""

import io
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUserFlex, DbSession
from app.core.base62 import id_to_code
from app.core.cache import delete_link_cache
from app.core.config import get_settings
from app.core.safe_browsing import update_link_safe_status
from app.core.security import hash_password
from app.core.urls import InvalidURLError, validate_url
from app.core.webhooks import fire_webhooks
from app.workers.webhook_worker import enqueue_webhook_delivery
from app.db.models import Link
from app.schemas.link import (
    BulkShortenRequest,
    BulkShortenResponse,
    BulkShortenResultItem,
    LinkCreate,
    LinkList,
    LinkPublic,
    LinkUpdate,
    ShortenResponse,
)

settings = get_settings()

router = APIRouter(tags=["links"])


def _short_url(code: str) -> str:
    """Kоddan to'liq qisqa havola hosil qiladi (base_url + code)."""
    return f"{settings.base_url.rstrip('/')}/{code}"


def _validate_or_400(url: str) -> str:
    """URL ni tekshiradi; xato bo'lsa 400."""
    try:
        return validate_url(url, resolve_dns=settings.url_resolve_dns)
    except InvalidURLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _get_owned_link(db: DbSession, user_id: int, code: str) -> Link:
    """Kod bo'yicha havolani topadi va EGAlikni tekshiradi.

    Topilmasa yoki boshqaniki bo'lsa — 404 (IDOR oldini olish).
    """
    link = await db.scalar(select(Link).where(Link.short_code == code))
    if link is None or link.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="havola topilmadi")
    return link


def _apply_utm(link: Link, payload) -> None:
    """UTM parametrlarini Link ob'ektiga qo'llaydi."""
    for field in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(link, field, val)


def _link_to_shorten_response(link: Link) -> ShortenResponse:
    return ShortenResponse(
        code=link.short_code,
        short_url=_short_url(link.short_code),
        long_url=link.long_url,
        expires_at=link.expires_at,
        created_at=link.created_at,
        has_password=link.password_hash is not None,
        safe_status=link.safe_status,
    )


# ─── POST /shorten ────────────────────────────────────────────────────────────

@router.post("/shorten", response_model=ShortenResponse, status_code=status.HTTP_201_CREATED)
async def shorten(
    payload: LinkCreate,
    user: CurrentUserFlex,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> ShortenResponse:
    """Qisqa havola yaratish.

    Mantiq:
      1) URL ni tekshirish (SSRF/sxema).
      2) custom_alias berilsa — bandligini tekshirib, kod sifatida ishlatish.
      3) Aks holda idempotentlik: shu egada shu URL allaqachon bormi?
      4) Yangi havola: avval yozamiz (id olamiz), so'ng id_to_code(id) bilan kod.
      5) Parol berilsa — argon2 hash saqlanadi.
      6) UTM parametrlari saqlanadi.
      7) Background task: Safe Browsing tekshiruvi.
      8) Background task: Webhook "link.created" hodisasi.
    """
    long_url = _validate_or_400(payload.url)

    # --- F3: maxsus alias ---
    if payload.custom_alias is not None:
        alias = payload.custom_alias
        taken = await db.scalar(select(Link).where(Link.short_code == alias))
        if taken is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="alias band")

        link = Link(
            short_code=alias,
            long_url=long_url,
            user_id=user.id,
            is_custom=True,
            expires_at=payload.expires_at,
            password_hash=hash_password(payload.password) if payload.password else None,
        )
        _apply_utm(link, payload)
        db.add(link)
        await db.commit()
        await db.refresh(link)

        background_tasks.add_task(update_link_safe_status, link.id, link.long_url)
        background_tasks.add_task(fire_webhooks, user.id, "link.created", {
            "code": link.short_code, "short_url": _short_url(link.short_code)
        })
        background_tasks.add_task(enqueue_webhook_delivery, user.id, "link.created", {
            "code": link.short_code, "short_url": _short_url(link.short_code)
        })
        return _link_to_shorten_response(link)

    # --- Idempotent shorten (alias bo'lmaganda, parolsiz) ---
    # Parolsiz bo'lsa idempotentlikni tekshiramiz
    if payload.password is None:
        existing = await db.scalar(
            select(Link).where(
                Link.user_id == user.id,
                Link.long_url == long_url,
                Link.is_active.is_(True),
                Link.is_custom.is_(False),
                Link.password_hash.is_(None),
            )
        )
        if existing is not None:
            return _link_to_shorten_response(existing)

    # --- Yangi avtomatik kod ---
    link = Link(
        short_code=f"__pending__{uuid.uuid4().hex}",
        long_url=long_url,
        user_id=user.id,
        is_custom=False,
        expires_at=payload.expires_at,
        password_hash=hash_password(payload.password) if payload.password else None,
    )
    _apply_utm(link, payload)
    db.add(link)
    await db.flush()  # id tayinlanadi (commit qilmasdan)
    link.short_code = id_to_code(link.id)
    await db.commit()
    await db.refresh(link)

    background_tasks.add_task(update_link_safe_status, link.id, link.long_url)
    background_tasks.add_task(fire_webhooks, user.id, "link.created", {
        "code": link.short_code, "short_url": _short_url(link.short_code)
    })
    background_tasks.add_task(enqueue_webhook_delivery, user.id, "link.created", {
        "code": link.short_code, "short_url": _short_url(link.short_code)
    })
    return _link_to_shorten_response(link)


# ─── GET /links ───────────────────────────────────────────────────────────────

@router.get("/links", response_model=LinkList)
async def list_links(
    user: CurrentUserFlex,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: int | None = Query(default=None, description="oxirgi ko'rilgan havola id'si"),
    q: str | None = Query(default=None, description="long_url bo'yicha qidiruv"),
) -> LinkList:
    """Mening havolalarim (kursorli paginatsiya + qidiruv)."""
    base_filter = [Link.user_id == user.id]
    if q:
        base_filter.append(Link.long_url.ilike(f"%{q}%"))

    total = await db.scalar(select(func.count()).select_from(Link).where(*base_filter)) or 0

    stmt = select(Link).where(*base_filter)
    if cursor is not None:
        stmt = stmt.where(Link.id < cursor)
    stmt = stmt.order_by(Link.id.desc()).limit(limit + 1)

    rows = list(await db.scalars(stmt))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    return LinkList(
        items=[LinkPublic.model_validate(link) for link in items],
        next_cursor=next_cursor,
        total=total,
    )


# ─── GET /links/{code} ────────────────────────────────────────────────────────

@router.get("/links/{code}", response_model=LinkPublic)
async def get_link(code: str, user: CurrentUserFlex, db: DbSession) -> Link:
    """Bitta havola metadatasi."""
    return await _get_owned_link(db, user.id, code)


# ─── PATCH /links/{code} ─────────────────────────────────────────────────────

@router.patch("/links/{code}", response_model=LinkPublic)
async def update_link(
    code: str,
    payload: LinkUpdate,
    user: CurrentUserFlex,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> Link:
    """Havolani yangilash — qisman, har qanday qism."""
    link = await _get_owned_link(db, user.id, code)

    if payload.long_url is not None:
        link.long_url = _validate_or_400(payload.long_url)
        # URL o'zgarganda Safe Browsing qayta tekshiriladi
        background_tasks.add_task(update_link_safe_status, link.id, link.long_url)

    if payload.custom_alias is not None and payload.custom_alias != link.short_code:
        taken = await db.scalar(select(Link).where(Link.short_code == payload.custom_alias))
        if taken is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="alias band")
        old_code = link.short_code
        link.short_code = payload.custom_alias
        link.is_custom = True
        # Eski keshni ham tozalaymiz
        background_tasks.add_task(delete_link_cache, old_code)

    if "expires_at" in payload.model_fields_set:
        link.expires_at = payload.expires_at

    if payload.is_active is not None:
        link.is_active = payload.is_active

    # Parol yangilash: None = olib tashlash, string = yangi parol
    if "password" in payload.model_fields_set:
        if payload.password is None:
            link.password_hash = None
        else:
            link.password_hash = hash_password(payload.password)

    # UTM yangilash
    _apply_utm(link, payload)

    await db.commit()
    await db.refresh(link)

    # Kesh tozalash
    background_tasks.add_task(delete_link_cache, link.short_code)
    return link


# ─── DELETE /links/{code} ─────────────────────────────────────────────────────

@router.delete("/links/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    code: str,
    user: CurrentUserFlex,
    db: DbSession,
    background_tasks: BackgroundTasks,
    hard: bool = Query(default=False, description="true = qatorni butunlay o'chirish"),
) -> Response:
    """Havolani o'chirish.

    Standart — SOFT delete (is_active=False): analitika saqlanadi.
    ?hard=true — HARD delete: qator butunlay o'chadi.
    """
    link = await _get_owned_link(db, user.id, code)
    link_data = {"code": link.short_code, "long_url": link.long_url}

    if hard:
        await db.delete(link)
    else:
        link.is_active = False

    await db.commit()

    # Kesh tozalash va webhook
    background_tasks.add_task(delete_link_cache, code)
    background_tasks.add_task(fire_webhooks, user.id, "link.deleted", link_data)
    background_tasks.add_task(enqueue_webhook_delivery, user.id, "link.deleted", link_data)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── GET /links/{code}/qr ─────────────────────────────────────────────────────

@router.get("/links/{code}/qr", response_class=StreamingResponse)
async def get_qr_code(code: str, user: CurrentUserFlex, db: DbSession) -> StreamingResponse:
    """QR kod generatsiyasi — PNG formatida qaytaradi.

    Foydalanuvchi havolasini QR kodga aylantirib beradi.
    Faqat EGA bo'lgan havola uchun ishlaydi.
    """
    link = await _get_owned_link(db, user.id, code)

    try:
        import qrcode  # type: ignore
        from qrcode.image.pil import PilImage  # type: ignore
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="qrcode paketi o'rnatilmagan",
        ) from exc

    short_url = _short_url(link.short_code)

    qr = qrcode.QRCode(
        version=None,  # Avtomatik hajm
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(short_url)
    qr.make(fit=True)

    img: PilImage = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="qr_{code}.png"',
            "Cache-Control": "public, max-age=3600",
        },
    )


# ─── POST /bulk-shorten ───────────────────────────────────────────────────────

@router.post("/bulk-shorten", response_model=BulkShortenResponse, status_code=status.HTTP_200_OK)
async def bulk_shorten(
    payload: BulkShortenRequest,
    user: CurrentUserFlex,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> BulkShortenResponse:
    """Bir so'rovda ≤50 ta URL qisqartiradi.

    Har URL alohida qayta ishlanadi. Biri xato bo'lsa qolganlar davom etadi.
    Javobda har URL uchun `ok` va `short_url` yoki `error` qaytariladi.
    """
    results: list[BulkShortenResultItem] = []
    success = 0
    errors = 0

    for item in payload.urls:
        # URL tekshirish
        try:
            long_url = validate_url(item.url, resolve_dns=settings.url_resolve_dns)
        except InvalidURLError as exc:
            results.append(BulkShortenResultItem(url=item.url, ok=False, error=str(exc)))
            errors += 1
            continue

        # custom_alias tekshirish
        if item.custom_alias is not None:
            taken = await db.scalar(
                select(Link).where(Link.short_code == item.custom_alias)
            )
            if taken is not None:
                results.append(BulkShortenResultItem(
                    url=item.url, ok=False, error=f"alias band: {item.custom_alias}"
                ))
                errors += 1
                continue
            short_code_to_use = item.custom_alias
            is_custom = True
        else:
            short_code_to_use = f"__pending__{uuid.uuid4().hex}"
            is_custom = False

        link = Link(
            short_code=short_code_to_use,
            long_url=long_url,
            user_id=user.id,
            is_custom=is_custom,
            expires_at=item.expires_at,
            utm_source=item.utm_source,
            utm_medium=item.utm_medium,
            utm_campaign=item.utm_campaign,
        )
        db.add(link)
        await db.flush()

        if not is_custom:
            link.short_code = id_to_code(link.id)

        await db.commit()
        await db.refresh(link)

        background_tasks.add_task(update_link_safe_status, link.id, link.long_url)

        results.append(BulkShortenResultItem(
            url=item.url,
            ok=True,
            short_url=_short_url(link.short_code),
            code=link.short_code,
        ))
        success += 1

    return BulkShortenResponse(
        results=results,
        success_count=success,
        error_count=errors,
    )
