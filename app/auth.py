import bcrypt
from fastapi import Request, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User

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
    Retrieves the currently authenticated user based on a secure session cookie.
    Returns None if the session cookie is missing or invalid.
    """
    user_id_str = request.cookies.get("session_user_id")
    if not user_id_str:
        return None
    try:
        user_id = int(user_id_str)
        return db.query(User).filter(User.id == user_id).first()
    except ValueError:
        return None
