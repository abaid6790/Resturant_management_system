"""Cash register sessions. cash_movements is append-only, same pattern as the other ledgers."""
from __future__ import annotations

from datetime import UTC, datetime

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.money import money
from app.extensions import db
from app.models.pos import CashMovement, CashRegisterSession
from app.services import audit


def open_session_for(location_id: int):
    return db.session.scalar(
        db.select(CashRegisterSession).where(CashRegisterSession.location_id == location_id,
                                             CashRegisterSession.status == "open")
    )


def visible_sessions(actor, location_id: int | None = None, status: str | None = None):
    stmt = db.select(CashRegisterSession).order_by(CashRegisterSession.id.desc())
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(CashRegisterSession.branch_id.in_(ids))
    if location_id:
        stmt = stmt.where(CashRegisterSession.location_id == location_id)
    if status:
        stmt = stmt.where(CashRegisterSession.status == status)
    return stmt


def get_session_or_404(actor, session_id: int) -> CashRegisterSession:
    ids = allowed_branch_ids(actor)
    sess = db.session.get(CashRegisterSession, session_id)
    if sess is None or (ids is not None and sess.branch_id not in ids):
        raise NotFoundError("Cash register session not found.")
    return sess


def _post(session: CashRegisterSession, movement_type: str, amount, *, reason=None,
         reference_type=None, reference_id=None, actor=None) -> CashMovement:
    mv = CashMovement(session_id=session.id, movement_type=movement_type, amount=money(amount),
                      reason=reason, reference_type=reference_type,
                      reference_id=None if reference_id is None else str(reference_id),
                      user_id=actor.id if actor else None)
    db.session.add(mv)
    return mv


def session_cash_total(session_id: int):
    return money(db.session.scalar(
        db.select(db.func.coalesce(db.func.sum(CashMovement.amount), 0))
        .where(CashMovement.session_id == session_id)
    ) or 0)


def open_session(actor, location, opening_float) -> CashRegisterSession:
    if open_session_for(location.id):
        raise BusinessRuleError(f"A cash register session is already open at {location.name}.")
    try:
        opening_float = money(opening_float)
        assert opening_float >= 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a valid opening amount.",
                              details={"opening_float": "Enter an amount of zero or more."}) from exc
    sess = CashRegisterSession(location_id=location.id, branch_id=location.branch_id,
                               opened_by=actor.id if actor else None, opening_float=opening_float,
                               status="open")
    db.session.add(sess)
    db.session.flush()
    _post(sess, "opening", opening_float, reason="Opening float", actor=actor)
    audit.log("cash_session.open", "cash", record_type="cash_session", record_id=sess.id,
             branch_id=location.branch_id, after={"opening_float": str(opening_float)})
    return sess


def cash_in(actor, session: CashRegisterSession, amount, reason: str) -> CashMovement:
    if session.status != "open":
        raise BusinessRuleError("This cash register session is already closed.")
    try:
        amount = money(amount)
        assert amount > 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a valid amount.",
                              details={"amount": "Enter an amount greater than zero."}) from exc
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.", details={"reason": "Required."})
    mv = _post(session, "cash_in", amount, reason=reason.strip(), actor=actor)
    audit.log("cash_session.cash_in", "cash", record_type="cash_session", record_id=session.id,
             branch_id=session.branch_id, after={"amount": str(amount)})
    return mv


def cash_out(actor, session: CashRegisterSession, amount, reason: str) -> CashMovement:
    if session.status != "open":
        raise BusinessRuleError("This cash register session is already closed.")
    try:
        amount = money(amount)
        assert amount > 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a valid amount.",
                              details={"amount": "Enter an amount greater than zero."}) from exc
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.", details={"reason": "Required."})
    mv = _post(session, "cash_out", -amount, reason=reason.strip(), actor=actor)
    audit.log("cash_session.cash_out", "cash", record_type="cash_session", record_id=session.id,
             branch_id=session.branch_id, after={"amount": str(amount)})
    return mv


def record_sale_cash(session: CashRegisterSession, amount, *, reference_type, reference_id, actor):
    """Internal: called by pos.complete_sale() for the cash portion of a payment."""
    return _post(session, "sale", amount, reason="Cash sale", reference_type=reference_type,
                reference_id=reference_id, actor=actor)


def close_session(actor, session: CashRegisterSession, counted_cash) -> CashRegisterSession:
    if session.status != "open":
        raise BusinessRuleError("This cash register session is already closed.")
    try:
        counted_cash = money(counted_cash)
        assert counted_cash >= 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a valid counted amount.",
                              details={"counted_cash": "Enter an amount of zero or more."}) from exc
    expected = session_cash_total(session.id)
    session.status = "closed"
    session.closed_by = actor.id if actor else None
    session.closed_at = datetime.now(UTC)
    session.counted_cash = counted_cash
    session.expected_cash = expected
    session.variance = money(counted_cash - expected)
    audit.log("cash_session.close", "cash", record_type="cash_session", record_id=session.id,
             branch_id=session.branch_id,
             after={"expected": str(expected), "counted": str(counted_cash),
                    "variance": str(session.variance)})
    return session
