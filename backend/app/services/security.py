"""Authentication, role-based access control and the audit trail.

Three roles:
  ADMIN     -- everything, including camera onboarding and user management
  OPERATOR  -- run ingest, acknowledge and resolve alerts, edit the watchlist
  ANALYST   -- read-only: search, view alerts, export reports

Passwords are hashed with bcrypt. The JWT secret comes from the environment; the
built-in default is a development placeholder and the app warns when it is used.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuditLog, Role, User

log = logging.getLogger("sentinel.security")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

DEV_SECRET = "dev-only-change-in-production"

#: bcrypt hashes at most 72 bytes of input and raises on anything longer.
_BCRYPT_MAX_BYTES = 72


def _encode(password: str) -> bytes:
    """Encode a password for bcrypt, truncated at its 72-byte limit.

    Truncation is on byte boundaries, not characters, so a multi-byte character
    straddling the limit cannot produce invalid UTF-8 in the hashed value.
    """
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(username: str, role: str) -> str:
    if settings.jwt_secret == DEV_SECRET:
        log.warning("JWT_SECRET is the development default -- set it in production")
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": username, "role": role, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def get_current_user(token: str | None = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_error
    try:
        payload = jwt.decode(token, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
    except JWTError:
        raise credentials_error from None
    if not username:
        raise credentials_error

    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.active:
        raise credentials_error
    return user


def require_roles(*roles: Role):
    """Dependency factory enforcing that the caller holds one of ``roles``."""

    allowed = set(roles)

    def guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(r.value for r in allowed)}",
            )
        return user

    return guard


require_admin = require_roles(Role.ADMIN)
require_operator = require_roles(Role.ADMIN, Role.OPERATOR)
require_any = require_roles(Role.ADMIN, Role.OPERATOR, Role.ANALYST)


def record_audit(db: Session, *, username: str, action: str, entity: str = "",
                 entity_id: str = "", detail: str = "",
                 request: Request | None = None) -> None:
    db.add(AuditLog(
        username=username,
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        detail=detail,
        ip_address=request.client.host if request and request.client else None,
    ))
    db.commit()


def list_audit(db: Session, *, limit: int = 200) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)))


def create_user(db: Session, *, username: str, password: str, role: str = "ANALYST",
                full_name: str = "") -> User:
    existing = db.scalar(select(User).where(User.username == username))
    if existing:
        return existing
    user = User(username=username, full_name=full_name or username,
                hashed_password=hash_password(password), role=Role(role))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def seed_default_users(db: Session) -> list[str]:
    """Create the demonstration accounts documented in the README."""
    defaults = [
        ("admin", "sentinel-admin", Role.ADMIN, "System Administrator"),
        ("operator", "sentinel-operator", Role.OPERATOR, "Control Room Operator"),
        ("analyst", "sentinel-analyst", Role.ANALYST, "Intelligence Analyst"),
    ]
    created = []
    for username, password, role, full_name in defaults:
        if db.scalar(select(User).where(User.username == username)) is None:
            create_user(db, username=username, password=password,
                        role=role.value, full_name=full_name)
            created.append(username)
    return created
