"""Sign-in, server-side sessions, lockout and password changes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import current_app, request

from app.core import security
from app.core.errors import AuthenticationError, ValidationError
from app.extensions import db
from app.models.auth import User, UserSession
from app.services import audit
from app.services import settings as settings_svc

GENERIC_FAIL = "Wrong username or password, or the account is locked."


def _now() -> datetime:
    return datetime.now(UTC)


def find_user(username: str) -> User | None:
    return db.session.scalar(
        db.select(User).where(db.func.lower(User.username) == (username or "").strip().lower())
    )


def authenticate(username: str, password: str) -> User:
    """Verify credentials; commits failure bookkeeping itself, because callers roll back on error."""
    user = find_user(username)
    if user is None:
        security.burn_verify(password)
        audit.log("login.failed", "auth", after={"username": (username or "")[:32]},
                  username=None)
        db.session.commit()
        raise AuthenticationError(GENERIC_FAIL)

    if user.locked_until and user.locked_until > _now():
        audit.log("login.blocked", "auth", user=user, record_type="user", record_id=user.id,
                  after={"reason": "locked"})
        db.session.commit()
        raise AuthenticationError(GENERIC_FAIL)

    if not user.is_active or not security.verify_password(user.password_hash, password):
        user.failed_logins += 1
        limit = settings_svc.get("security.max_failed_logins")
        if user.failed_logins >= limit:
            user.locked_until = _now() + timedelta(minutes=settings_svc.get("security.lockout_minutes"))
            user.failed_logins = 0
            audit.log("user.locked", "auth", user=user, record_type="user", record_id=user.id,
                      after={"locked_until": user.locked_until})
        audit.log("login.failed", "auth", user=user, record_type="user", record_id=user.id,
                  after={"inactive": not user.is_active} if not user.is_active else None)
        db.session.commit()
        raise AuthenticationError(GENERIC_FAIL)

    user.failed_logins, user.locked_until, user.last_login_at = 0, None, _now()
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
    return user


def start_session(user: User) -> str:
    token = security.new_token()
    idle = timedelta(hours=current_app.config["SESSION_ABSOLUTE_HOURS"])
    db.session.add(UserSession(
        token_hash=security.hash_token(token), user_id=user.id, ip=request.remote_addr,
        user_agent=(request.user_agent.string or "")[:255], expires_at=_now() + idle,
    ))
    audit.log("login", "auth", user=user, record_type="user", record_id=user.id)
    return token


def load_session(token: str) -> tuple[UserSession, User] | None:
    sess = db.session.scalar(
        db.select(UserSession).where(UserSession.token_hash == security.hash_token(token))
    )
    if sess is None or sess.revoked_at is not None:
        return None
    now = _now()
    idle = timedelta(minutes=settings_svc.get("security.session_idle_minutes"))
    if sess.expires_at <= now or now - sess.last_seen_at > idle:
        sess.revoked_at = now
        db.session.commit()
        return None
    user = db.session.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    if now - sess.last_seen_at > timedelta(seconds=60):  # avoid a write on every request
        sess.last_seen_at = now
        db.session.commit()
    return sess, user


def end_session(sess: UserSession, user: User) -> None:
    sess.revoked_at = _now()
    audit.log("logout", "auth", user=user, record_type="user", record_id=user.id)


def revoke_all_sessions(user_id: int, except_id: int | None = None) -> None:
    q = db.update(UserSession).where(UserSession.user_id == user_id,
                                     UserSession.revoked_at.is_(None))
    if except_id is not None:
        q = q.where(UserSession.id != except_id)
    db.session.execute(q.values(revoked_at=_now()))


def check_password(password: str, username: str) -> None:
    problems = security.password_problems(
        password, username=username, min_length=settings_svc.get("security.password_min_length")
    )
    if problems:
        raise ValidationError("That password is not acceptable.", details={"password": " ".join(problems)})


def set_password(target: User, new_password: str, actor: User, *, must_change: bool,
                 keep_session_id: int | None = None, action: str = "password.change") -> None:
    check_password(new_password, target.username)
    target.password_hash = security.hash_password(new_password)
    target.password_changed_at = _now()
    target.must_change_password = must_change
    target.failed_logins, target.locked_until = 0, None
    revoke_all_sessions(target.id, except_id=keep_session_id)
    audit.log(action, "users", user=actor, record_type="user", record_id=target.id,
              after={"must_change_password": must_change})
