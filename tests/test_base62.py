"""base62 kodlash birlik (unit) testlari — tarmoq/DB kerak emas."""

import pytest

from app.core.base62 import (
    CODE_LENGTH,
    code_to_id,
    decode_int,
    encode_int,
    id_to_code,
)


def test_encode_known_values():
    # Spec §5 dagi misollar.
    assert encode_int(1) == "1"
    assert encode_int(125) == "21"
    assert encode_int(999999) == "4c91"


def test_encode_decode_roundtrip():
    for n in [0, 1, 61, 62, 1000, 123456789]:
        assert decode_int(encode_int(n)) == n


def test_id_to_code_is_fixed_length_and_reversible():
    for identifier in [1, 2, 100, 5000, 999_999]:
        code = id_to_code(identifier)
        # Kod har doim 7 belgi (padding).
        assert len(code) == CODE_LENGTH
        # To'liq teskari: kod -> id.
        assert code_to_id(code) == identifier


def test_codes_are_unique_and_unguessable():
    # Ketma-ket id'lar har xil (to'qnashuvsiz) va ketma-ket bo'lib ko'rinmaydigan kod beradi.
    codes = {id_to_code(i) for i in range(1, 1000)}
    assert len(codes) == 999  # hammasi noyob
    # Qo'shni id'lar kodi bir xil emas.
    assert id_to_code(1) != id_to_code(2)


def test_decode_invalid_char_raises():
    with pytest.raises(ValueError):
        decode_int("!!!")
