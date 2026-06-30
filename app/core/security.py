"""Parol hashlash (argon2) va token hashlash (SHA-256).

NEGA argon2?
  - "Sekin" va "memory-hard" — GPU bilan ko'plab parolni sinab ko'rishni
    qimmatga tushiradi. bcrypt ham yaxshi, lekin argon2id zamonaviyroq.
  - Hashda tuz (salt) avtomatik qo'shiladi — bir xil parol har xil hash beradi.

NEGA tokenlarni SHA-256 bilan hashlaymiz?
  - Refresh token DB da saqlanadi. Agar DB sizib chiqsa, hujumchi
    tokenlarning O'ZINI emas, faqat hashini ko'radi — ulardan foydalana olmaydi.
  - Parollardan farqli, token allaqachon tasodifiy/uzun, shuning uchun
    tez SHA-256 yetarli (argon2 shart emas).
"""

import hashlib

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Standart parametrlar bilan hasher (xohlasangiz time_cost/memory_cost sozlanadi).
_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    """Ochiq parolni argon2 hashga aylantiradi (saqlash uchun)."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Kiritilgan parol hashga mos kelishini tekshiradi.

    True/False qaytaradi — istisno (exception) ni yutib yuboramiz,
    chunki chaqiruvchi tomonga oddiy "to'g'ri/noto'g'ri" kerak.
    """
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """Hash eski parametrlar bilan yaratilganmi? (xavfsizlikni yangilash uchun)."""
    return _hasher.check_needs_rehash(password_hash)


def hash_token(token: str) -> str:
    """Tokenni SHA-256 hex hashga aylantiradi (DB da saqlash/qidirish uchun)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()