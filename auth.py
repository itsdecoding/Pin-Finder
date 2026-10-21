import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from database import get_connection

SECRET_KEY = "pin-finder-internal-super-secret-key-change-in-prod"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against the bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Generate a JWT bearer token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT bearer token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None

def register_user(username: str, email: str, password: str, role: str = "admin") -> Optional[int]:
    """Register a new user in the database."""
    conn = get_connection()
    cur = conn.cursor()
    hashed = hash_password(password)
    try:
        cur.execute(
            "INSERT INTO users (username, email, hashed_password, role) VALUES (?, ?, ?, ?);",
            (username.strip().lower(), email.strip().lower(), hashed, role)
        )
        user_id = cur.lastrowid
        conn.commit()
        return user_id
    except Exception:
        return None
    finally:
        conn.close()

def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Verify credentials and return user info."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, email, hashed_password, role FROM users WHERE username = ? OR email = ?;",
        (username.strip().lower(), username.strip().lower())
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    user = dict(row)
    if verify_password(password, user["hashed_password"]):
        del user["hashed_password"]
        return user
    return None
