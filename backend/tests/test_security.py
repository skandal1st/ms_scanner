"""Шифрование токенов (Fernet) и JWT — критично: секреты МС/ЧЗ хранятся под Fernet."""

import pytest

from app.core.security import (
    encrypt_token,
    decrypt_token,
    create_access_token,
    create_refresh_token,
    decode_token,
)


def test_encrypt_decrypt_roundtrip():
    secret = "ms-token-xyz-123"
    enc = encrypt_token(secret)
    assert enc != secret            # не хранится в открытом виде
    assert decrypt_token(enc) == secret


def test_encrypt_is_nondeterministic():
    # Fernet добавляет IV/таймстамп — два шифротекста одной строки различаются.
    assert encrypt_token("same") != encrypt_token("same")


def test_access_token_type_and_sub():
    tok = create_access_token({"sub": "user-1"})
    payload = decode_token(tok)
    assert payload["sub"] == "user-1"
    assert payload["type"] == "access"


def test_refresh_token_type():
    payload = decode_token(create_refresh_token({"sub": "user-1"}))
    assert payload["type"] == "refresh"


def test_decode_rejects_tampered_token():
    from jose import JWTError

    tok = create_access_token({"sub": "user-1"})
    tampered = tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")
    with pytest.raises(JWTError):
        decode_token(tampered)
