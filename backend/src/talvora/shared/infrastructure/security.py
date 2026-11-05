"""Security Utilities: Tokens, Hashes, and Cryptographic Operations."""

import hashlib
import hmac
import secrets


def generate_token_256() -> str:
    """Generate a cryptographically secure 256-bit URL-safe token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash an opaque token using SHA-256 for persistent database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_compare(val1: str, val2: str) -> bool:
    """Compare two strings in constant time to defeat timing analysis."""
    return hmac.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))
