"""Unit tests for security utilities, CSRF, encryption, and safe redirect validation."""

import pytest
from cryptography.exceptions import InvalidTag

from talvora.shared.infrastructure.security import (
    constant_time_compare,
    decrypt_token,
    encrypt_token,
    generate_csrf_token,
    generate_token_256,
    hash_token,
    is_safe_redirect_url,
    verify_csrf_token,
)


def test_token_generation_and_hashing() -> None:
    token = generate_token_256()
    assert len(token) >= 32
    h1 = hash_token(token)
    h2 = hash_token(token)
    assert h1 == h2
    assert len(h1) == 64


def test_constant_time_compare() -> None:
    assert constant_time_compare("secret_string_123", "secret_string_123") is True
    assert constant_time_compare("secret_string_123", "secret_string_456") is False


def test_csrf_token_generation_and_verification() -> None:
    secret = "test-secret-key-must-be-secure"
    session_id = "session-uuid-1234"

    token = generate_csrf_token(secret, session_id)
    assert verify_csrf_token(secret, session_id, token) is True

    # Tampered token
    assert verify_csrf_token(secret, session_id, token + "x") is False

    # Wrong session ID
    assert verify_csrf_token(secret, "other-session-5678", token) is False

    # Wrong secret
    assert verify_csrf_token("different-secret", session_id, token) is False

    # Malformed token
    assert verify_csrf_token(secret, session_id, "invalid_no_colon") is False
    assert verify_csrf_token(secret, session_id, "") is False


def test_safe_redirect_url_validation() -> None:
    # Safe relative paths
    assert is_safe_redirect_url("/dashboard") is True
    assert is_safe_redirect_url("/app/jobs/123") is True
    assert is_safe_redirect_url("/") is True

    # Unsafe / open redirect attack vectors
    assert is_safe_redirect_url("http://evil.com") is False
    assert is_safe_redirect_url("https://evil.com/phish") is False
    assert is_safe_redirect_url("//evil.com") is False
    assert is_safe_redirect_url("/\\evil.com") is False
    assert is_safe_redirect_url("javascript:alert(1)") is False
    assert is_safe_redirect_url("") is False
    assert is_safe_redirect_url("/path\\with\\backslash") is False


def test_aes_gcm_token_encryption_and_decryption() -> None:
    key = "super-secret-encryption-key-for-aes-256"
    plaintext = "my_refresh_token_123456789_very_confidential"

    encrypted = encrypt_token(plaintext, key)
    assert encrypted != plaintext

    decrypted = decrypt_token(encrypted, key)
    assert decrypted == plaintext

    # Decrypt with wrong key raises error
    with pytest.raises(InvalidTag):
        decrypt_token(encrypted, "wrong-key-different-secret-key-1234")
