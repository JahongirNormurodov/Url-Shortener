"""URL tekshirish (validation) va SSRF himoyasi (spec §13).

NEGA bu kerak?
  - Foydalanuvchi ixtiyoriy URL beradi. Biz uni keyinchalik redirect qilamiz,
    shuning uchun XAVFLI manzillarni boshidanoq rad etishimiz kerak:
      * `javascript:`, `data:`, `file:` — brauzerda kod ishga tushishi yoki
        mahalliy fayllarni ochishi mumkin (open-redirect / XSS xavfi).
      * `localhost`, `127.0.0.1`, ichki tarmoq IP lari (10.x, 192.168.x, ...) —
        SSRF (Server-Side Request Forgery): bizning serverimiz orqali ichki
        xizmatlarga so'rov yuborishga urinish.

Bu modul "sof" funksiyalardan iborat (DB/tarmoqqa bog'liq emas) — shuning uchun
oson test qilinadi.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Faqat shu sxemalarga ruxsat (spec §13: "scheme must be http/https").
ALLOWED_SCHEMES = {"http", "https"}

# Spec §13: "max URL length 2048".
MAX_URL_LENGTH = 2048


class InvalidURLError(ValueError):
    """URL tekshiruvdan o'tmaganda tashlanadigan istisno.

    ValueError'dan meros oladi, lekin alohida tur — route qatlamida uni
    400/422 javobga aylantirish oson bo'ladi.
    """


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """IP manzil "xavfli" (ichki/maxsus) toifaga kiradimi?

    is_private    — 10.x, 172.16-31.x, 192.168.x (ichki tarmoq)
    is_loopback   — 127.x, ::1
    is_link_local — 169.254.x (auto-config)
    is_reserved / is_multicast / is_unspecified — boshqa maxsus diapazonlar
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(raw_url: str, *, resolve_dns: bool = True) -> str:
    """URL ni tekshiradi va "tozalangan" ko'rinishini qaytaradi.

    Bosqichlar:
      1) Uzunlik va boshqaruv belgilarini tekshirish.
      2) Sxema http/https ekanligini tekshirish.
      3) Host mavjudligini tekshirish.
      4) SSRF: host IP bo'lsa darrov tekshirish; domen bo'lsa DNS orqali
         IP ga aylantirib, ichki diapazonga tushmasligini tekshirish.

    Xato bo'lsa InvalidURLError tashlaydi.

    resolve_dns=False — testlarda tarmoqqa chiqmaslik uchun DNS bosqichini
    o'tkazib yuborish imkonini beradi.
    """
    if not raw_url or not raw_url.strip():
        raise InvalidURLError("URL bo'sh bo'lishi mumkin emas")

    url = raw_url.strip()

    if len(url) > MAX_URL_LENGTH:
        raise InvalidURLError(f"URL juda uzun (maks. {MAX_URL_LENGTH} belgi)")

    # Boshqaruv belgilari (yangi qator, tab, \0 va h.k.) — rad etamiz.
    if any(ord(ch) < 0x20 for ch in url):
        raise InvalidURLError("URL da boshqaruv belgilari bo'lishi mumkin emas")

    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidURLError(
            f"ruxsat etilmagan sxema: {parsed.scheme!r} (faqat http/https)"
        )

    host = parsed.hostname
    if not host:
        raise InvalidURLError("URL da host (domen) yo'q")

    # Host to'g'ridan-to'g'ri IP bo'lishi mumkin (masalan http://127.0.0.1).
    # DIQQAT: InvalidURLError — ValueError vorisi. Agar bloklash `raise`'ini
    # `try/except ValueError` ichida qilsak, except uni "yutib" yuboradi.
    # Shuning uchun avval IP ni alohida aniqlaymiz, keyin bloklaymiz.
    parsed_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        parsed_ip = None  # Host IP emas — bu oddiy domen.

    if parsed_ip is not None:
        if _is_blocked_ip(parsed_ip):
            raise InvalidURLError("ichki/maxsus IP manzillarga ruxsat yo'q (SSRF)")
        return url

    # Aniq "localhost" nomini ham bloklaymiz.
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise InvalidURLError("localhost ga ruxsat yo'q (SSRF)")

    if resolve_dns:
        # Domenni IP ga aylantiramiz va har bir natijani tekshiramiz.
        # Hujumchi domeni ichki IP ga "ishora qilishi" mumkin — shuni ushlaymiz.
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise InvalidURLError(f"domenni aniqlab bo'lmadi: {host}") from exc

        for info in infos:
            sockaddr = info[4]
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_blocked_ip(ip):
                raise InvalidURLError(
                    "domen ichki/maxsus IP manzilga ishora qilmoqda (SSRF)"
                )

    return url
