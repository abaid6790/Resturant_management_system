"""Application error types and the single JSON error envelope."""
from __future__ import annotations

import logging

from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, code: str | None = None, details=None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        if code:
            self.code = code
        self.details = details


class BadRequestError(AppError):
    status_code, code, default_message = 400, "bad_request", "Malformed request."


class AuthenticationError(AppError):
    status_code, code, default_message = 401, "authentication_required", "Authentication required."


class PermissionDeniedError(AppError):
    status_code, code, default_message = 403, "permission_denied", "You do not have permission."


class NotFoundError(AppError):
    status_code, code, default_message = 404, "not_found", "Resource not found."


class ConflictError(AppError):
    """Duplicates, idempotency clashes, stale updates."""

    status_code, code, default_message = 409, "conflict", "Request conflicts with current state."


class TooManyRequestsError(AppError):
    status_code, code, default_message = 429, "rate_limited", "Too many attempts. Try again later."


class ValidationError(AppError):
    status_code, code, default_message = 422, "validation_error", "Validation failed."


class BusinessRuleError(AppError):
    """Valid input that violates a rule (over-refund, insufficient stock, closed register...)."""

    status_code, code, default_message = 422, "business_rule_violation", "Business rule violated."


def wants_json() -> bool:
    return (request.path.startswith("/api/") or request.is_json
            or request.accept_mimetypes.best == "application/json")


def error_response(status: int, code: str, message: str, details=None):
    if not wants_json():
        return render_template("errors/error.html", status=status, message=message), status
    body = {
        "error": {"code": code, "message": message},
        "request_id": getattr(g, "request_id", None),
    }
    if details is not None:
        body["error"]["details"] = details
    return jsonify(body), status


def register_error_handlers(app: Flask) -> None:
    from app.extensions import db

    @app.errorhandler(AuthenticationError)
    def _auth_error(err: AuthenticationError):
        if wants_json():
            return error_response(err.status_code, err.code, err.message)
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.errorhandler(AppError)
    def _app_error(err: AppError):
        return error_response(err.status_code, err.code, err.message, err.details)

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        code = (err.name or "error").lower().replace(" ", "_")
        return error_response(err.code or 500, code, err.description or err.name)

    @app.errorhandler(IntegrityError)
    def _integrity(err: IntegrityError):
        db.session.rollback()
        log.warning("Integrity error: %s", err.orig)
        # Never leak SQL/constraint internals to the client.
        return error_response(409, "integrity_error", "The change conflicts with existing data.")

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):
        db.session.rollback()
        log.exception("Unhandled error")
        return error_response(500, "internal_error", "An unexpected error occurred.")
