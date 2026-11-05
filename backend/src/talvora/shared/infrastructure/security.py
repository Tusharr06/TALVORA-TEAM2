"""Security Utilities: Tokens, Hashes, Encryption, and Cryptographic Operations."""

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def generate_token_256() -> str:
    """Generate a cryptographically secure 256-bit URL-safe token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash an opaque token using SHA-256 for persistent database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_compare(val1: str, val2: str) -> bool:
    """Compare two strings in constant time to defeat timing analysis."""
    return hmac.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))


def generate_csrf_token(secret: str, session_id: str) -> str:
    """Generate a cryptographically bound Anti-CSRF token for an active session."""
    salt = secrets.token_hex(8)
    msg = f"{session_id}:{salt}".encode()
    signature = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{salt}:{signature}"


def verify_csrf_token(secret: str, session_id: str, token: str) -> bool:
    """Verify an Anti-CSRF token in constant time."""
    if not token or ":" not in token:
        return False
    parts = token.split(":", 1)
    if len(parts) != 2:
        return False
    salt, signature = parts
    msg = f"{session_id}:{salt}".encode()
    expected = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_safe_redirect_url(target: str) -> bool:
    """Validate that redirect target is a safe relative path, defeating open-redirect attacks."""
    if not target or not isinstance(target, str):
        return False
    target = target.strip()
    if not target.startswith("/"):
        return False
    # Reject protocol-relative URLs (//example.com) and backslashes (/\example.com)
    if target.startswith("//") or target.startswith("/\\"):
        return False
    return "\\" not in target


def _derive_32byte_key(key: str) -> bytes:
    """Derive 32 bytes from key string for AES-256."""
    return hashlib.sha256(key.encode("utf-8")).digest()


def encrypt_token(plaintext: str, key_str: str) -> str:
    """Encrypt sensitive token at rest using AES-256-GCM."""
    key = _derive_32byte_key(key_str)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt_token(encoded: str, key_str: str) -> str:
    """Decrypt sensitive token at rest using AES-256-GCM."""
    raw = base64.b64decode(encoded.encode("utf-8"))
    if len(raw) < 12:
        raise ValueError("Invalid encrypted token format")
    nonce = raw[:12]
    ciphertext = raw[12:]
    key = _derive_32byte_key(key_str)
    aesgcm = AESGCM(key)
    decrypted = aesgcm.decrypt(nonce, ciphertext, None)
    return decrypted.decode("utf-8")
