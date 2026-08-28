"""Fail-fast на слабые секреты в production (см. Settings._guard_prod_secrets)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, _DEFAULT_SECRET_KEY


def test_dev_allows_weak_keys():
    s = Settings(APP_ENV="dev", SECRET_KEY=_DEFAULT_SECRET_KEY, ENCRYPTION_KEY="")
    assert s.is_production is False


def test_production_rejects_default_secret():
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            SECRET_KEY=_DEFAULT_SECRET_KEY,
            ENCRYPTION_KEY="x" * 44,
        )


def test_production_rejects_empty_encryption_key():
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            SECRET_KEY="a-strong-secret-value-not-default",
            ENCRYPTION_KEY="",
        )


def test_production_ok_with_strong_keys():
    from cryptography.fernet import Fernet

    s = Settings(
        APP_ENV="production",
        SECRET_KEY="a-strong-secret-value-not-default",
        ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    assert s.is_production is True
