import pytest
from app.auth import (
    hash_password, verify_password, sign_user_id, verify_user_id
)

def test_password_hashing():
    pw = "my-flatmate-pass"
    hashed = hash_password(pw)
    
    assert hashed != pw
    assert len(hashed) > 10
    
    # Verify matches
    assert verify_password(pw, hashed) is True
    # Verify case sensitivity
    assert verify_password("My-flatmate-pass", hashed) is False

def test_password_verification_invalid():
    hashed = hash_password("secret")
    assert verify_password("wrong", hashed) is False

def test_cookie_signing_and_verification():
    user_id = 42
    signed = sign_user_id(user_id)
    
    # Structure check: user_id.expiry.signature
    assert "." in signed
    parts = signed.split(".")
    assert len(parts) == 3
    assert parts[0] == "42"
    
    # Valid signature checks out
    assert verify_user_id(signed) == 42

def test_invalid_cookie_handling():
    # Null or empty
    assert verify_user_id("") is None
    assert verify_user_id(None) is None
    
    # No dot separator
    assert verify_user_id("42") is None
    assert verify_user_id("invalid-cookie-value") is None

def test_tampered_cookie_handling():
    user_id = 42
    signed = sign_user_id(user_id)
    user_str, expiry_str, signature = signed.split(".")
    
    # Tamper with the user ID
    tampered_user = f"43.{expiry_str}.{signature}"
    assert verify_user_id(tampered_user) is None
    
    # Tamper with the signature value
    tampered_sig = f"{user_str}.{expiry_str}.{signature[:-2]}ab"
    assert verify_user_id(tampered_sig) is None
