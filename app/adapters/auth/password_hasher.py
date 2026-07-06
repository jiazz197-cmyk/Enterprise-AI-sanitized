"""Password hashing adapter wrapping bcrypt."""

from __future__ import annotations

from app.core.security import hash_password, verify_password
from app.ports.outbound.auth import PasswordHasherPort


class BcryptPasswordHasherAdapter(PasswordHasherPort):
    """Bcrypt-based password hashing via core.security primitives."""

    def hash_password(self, password: str) -> str:
        return hash_password(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return verify_password(plain_password, hashed_password)
