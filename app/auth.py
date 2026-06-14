import bcrypt
import hmac
import hashlib
import os
from fastapi import Request, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User

# HMAC key used to sign session cookies, fetched from environment in production
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-flatmate-key").encode("utf-8")

def sign_user_id(user_id: int) -> str:
    """
    Cryptographically signs a user ID for secure session cookies.
    """
    user_str = str(user_id)
    signature = hmac.new(SECRET_KEY, user_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{user_str}.{signature}"

def verify_user_id(cookie_value: str) -> int | None:
    """
    Verifies the signature of a session cookie and returns the user ID if authentic.
    """
    if not cookie_value or "." not in cookie_value:
        return None
    try:
        user_str, signature = cookie_value.split(".", 1)
        expected = hmac.new(SECRET_KEY, user_str.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            return int(user_str)
    except Exception:
        pass
    return None

def hash_password(password: str) -> str:
    """
    Hashes a plain-text password using a secure bcrypt salt.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")

def verify_password(password: str, hashed_password: str) -> bool:
    """
    Verifies a plain-text password against a stored bcrypt hash.
    """
    return bcrypt.checkpw(
        password.encode("utf-8"), 
        hashed_password.encode("utf-8")
    )

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """
    Retrieves the currently authenticated user based on a signed session cookie.
    Returns None if the cookie is missing, tampered with, or invalid.
    """
    cookie_value = request.cookies.get("session_user_id")
    if not cookie_value:
        return None
    user_id = verify_user_id(cookie_value)
    if user_id is None:
        return None
    return db.query(User).filter(User.id == user_id).first()
