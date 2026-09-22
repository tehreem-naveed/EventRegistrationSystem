"""
Shared API helpers: the response envelope and the global exception handler.

Every response has one of two shapes:

    success: {"success": true,  "data": ...}
    failure: {"success": false, "error": "<human message>", "code": "<machine code>",
              "details": {...}}          # "details" only for field-validation errors
"""
import logging

from django.db import OperationalError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def success_response(data, status_code=status.HTTP_200_OK):
    return Response({"success": True, "data": data}, status=status_code)


def error_response(message, code, status_code, details=None):
    body = {"success": False, "error": message, "code": code}
    if details is not None:
        body["details"] = details
    return Response(body, status=status_code)


def exception_handler(exc, context):
    """Convert every exception into the standard error envelope."""
    response = drf_exception_handler(exc, context)

    # Not a DRF-recognised exception.
    if response is None:
        if isinstance(exc, OperationalError):
            # e.g. "database is locked" after waiting for the SQLite write lock.
            # The atomic() block has already rolled back, so nothing is half-saved.
            logger.exception("Database operational error in %s", context.get("view"))
            return error_response(
                "The service is busy. Please try again.",
                "service_unavailable",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Never leak stack traces to API clients; log them server-side instead.
        logger.exception("Unhandled error in %s", context.get("view"))
        return error_response(
            "Internal server error.", "server_error", status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    if isinstance(exc, ValidationError):
        response.data = {
            "success": False,
            "error": "Validation failed.",
            "code": "validation_error",
            "details": exc.detail,
        }
        return response

    # NotAuthenticated, PermissionDenied, NotFound, MethodNotAllowed, our custom
    # 409 errors, ... DRF puts the message in response.data["detail"] (an
    # ErrorDetail that also carries the error code).
    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    response.data = {
        "success": False,
        "error": str(detail) if detail else "Request failed.",
        "code": getattr(detail, "code", "error"),
    }
    return response
