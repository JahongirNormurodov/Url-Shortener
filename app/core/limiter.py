"""Rate-limiting — slowapi orqali so'rovlar chastotasini cheklash.

slowapi — Flask-Limiter ning FastAPI uchun portirovkasi.
Redis backend ishlatilsa — distributed limiting (bir necha server instance'da).
Redis yo'q bo'lsa — in-memory (faqat bitta instance uchun).

Cheklovlar (config.py dan o'qiladi):
  - POST /shorten       — 20/minute
  - GET  /{code}        — 120/minute
  - POST /bulk-shorten  — 5/minute
  - POST /auth/login    — 10/minute

Identifikatsiya: IP manzil bo'yicha (autentifikatsiyasiz endpointlar uchun).
Autentifikatsiyalangan endpointlar uchun user_id bo'yicha ham bo'lishi mumkin,
lekin oddiylik uchun IP'dan boshlaymiz.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

settings = get_settings()

# Limiter singleton — butun ilova bo'ylab bitta ob'ekt.
# storage_uri — Redis URL; bu yerda rate-limit ma'lumotlari saqlanadi.
# key_func — har so'rov uchun "identifikator" (standart: IP manzil).
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    # enabled=False bo'lsa — barcha limitlar o'chiriladi (testlarda foydali).
    enabled=settings.rate_limit_enabled,
)
