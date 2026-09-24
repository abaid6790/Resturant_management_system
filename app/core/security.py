"""Password hashing (Argon2id), password policy and opaque tokens."""
from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import current_app

_COMMON = {"password", "password1", "12345678", "123456789", "qwerty123", "admin123", "letmein1"}


@lru_cache(maxsize=4)
def _hasher(time_cost: int, memory_kib: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism)


def _h() -> PasswordHasher:
    c = current_app.config
    return _hasher(c["ARGON2_TIME_COST"], c["ARGON2_MEMORY_KIB"], c["ARGON2_PARALLELISM"])


def hash_password(password: str) -> str:
    return _h().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _h().verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _h().check_needs_rehash(password_hash)


def burn_verify(password: str) -> None:
    """Spend the same time as a real verify so unknown usernames aren't distinguishable by timing."""
    verify_password(_dummy_hash(_h()), password)


@lru_cache(maxsize=4)
def _dummy_hash(hasher: PasswordHasher) -> str:
    return hasher.hash("not-a-real-password")


def password_problems(password: str, *, username: str = "", min_length: int = 8) -> list[str]:
    problems = []
    if len(password) < min_length:
        problems.append(f"Use at least {min_length} characters.")
    if len(password) > 128:
        problems.append("Use at most 128 characters.")
    if username and password.lower() == username.lower():
        problems.append("The password must not be the same as the username.")
    if password.lower() in _COMMON or len(set(password)) == 1:
        problems.append("That password is too common. Choose something less guessable.")
    return problems


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
