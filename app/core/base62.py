"""base62 kodlash + taxmin qilib bo'lmaydigan aralashtirish.

Bu modul DB dagi ketma-ket butun son ID (1, 2, 3, ...) ni
qisqa, taxmin qilib bo'lmaydigan matn kodga aylantiradi va aksincha.

Ikki bosqich:
  1) ID -> aralashtirilgan son   (modular ko'paytirish, qaytariladigan)
  2) aralashtirilgan son -> matn  (base62 kodlash)

Hammasi BIJEKSIYA: har xil ID har doim har xil kod beradi (to'qnashuvsiz),
lekin ketma-ket ID lar tasodifiy ko'rinadigan kodlarga aylanadi.
"""

# 62 ta belgi: raqamlar, kichik, katta harflar. Tartibi MUHIM —
# o'zgartirsangiz, eski kodlar boshqa songa "ochiladi".
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)  # 62

# Kod uzunligi va kodlash maydoni (domain).
CODE_LENGTH = 7
MODULUS = BASE**CODE_LENGTH  # 62^7 ≈ 3.52e12

# Aralashtirish kaliti: MODULUS bilan O'ZARO TUB bo'lishi shart,
# aks holda bijeksiya bo'lmaydi. MODULUS = 62^7 = 2^7 * 31^7,
# shuning uchun MULTIPLIER toq va 31 ga bo'linmasligi kerak.
# Quyidagi katta tub son shu shartlarni qondiradi.
MULTIPLIER = 1_500_450_271
# Modular teskari element: (x * MULTIPLIER * INVERSE) % MODULUS == x
# Python 3.8+ da pow(a, -1, m) modular teskarini beradi.
INVERSE = pow(MULTIPLIER, -1, MODULUS)


def encode_int(num: int) -> str:
    """Manfiy bo'lmagan butun sonni base62 matnga aylantiradi (paddingsiz).

    Misol: 125 -> "21", 999999 -> "4c91".
    """
    if num < 0:
        raise ValueError("encode_int faqat manfiy bo'lmagan sonlarni qabul qiladi")
    if num == 0:
        return ALPHABET[0]
    digits: list[str] = []
    while num > 0:
        num, rem = divmod(num, BASE)
        digits.append(ALPHABET[rem])
    # Qoldiqlarni teskari tartibda yig'dik, shuning uchun aylantiramiz.
    return "".join(reversed(digits))


# Tez qidirish uchun: belgi -> qiymat (har chaqiruvda qayta qurmaslik).
_CHAR_TO_VALUE = {char: index for index, char in enumerate(ALPHABET)}


def decode_int(code: str) -> int:
    """base62 matnni butun songa qaytaradi (encode_int ning teskarisi)."""
    num = 0
    for char in code:
        value = _CHAR_TO_VALUE.get(char)
        if value is None:
            raise ValueError(f"base62 da yo'q belgi: {char!r}")
        num = num * BASE + value
    return num


def obfuscate(identifier: int) -> int:
    """Ketma-ket ID ni aralashtiradi (qaytariladigan).

    (id * MULTIPLIER) % MODULUS — natija [0, MODULUS) oralig'ida,
    har xil id har xil natija (bijeksiya).
    """
    if not 0 <= identifier < MODULUS:
        raise ValueError(f"id 0 .. {MODULUS - 1} oralig'ida bo'lishi kerak")
    return (identifier * MULTIPLIER) % MODULUS


def deobfuscate(value: int) -> int:
    """obfuscate ning teskarisi: aralashgan sondan asl id ni tiklaydi."""
    return (value * INVERSE) % MODULUS


def id_to_code(identifier: int) -> str:
    """DB id -> qisqa kod (aralashtirish + base62 + 7 belgigacha to'ldirish).

    To'ldirish (padding) tufayli barcha kodlar bir xil uzunlikda (7) ko'rinadi.
    """
    obfuscated = obfuscate(identifier)
    code = encode_int(obfuscated)
    # Chap tomondan '0' (ALPHABET[0]) bilan 7 belgigacha to'ldiramiz.
    return code.rjust(CODE_LENGTH, ALPHABET[0])


def code_to_id(code: str) -> int:
    """Qisqa kod -> DB id (id_to_code ning to'liq teskarisi)."""
    obfuscated = decode_int(code)
    return deobfuscate(obfuscated)