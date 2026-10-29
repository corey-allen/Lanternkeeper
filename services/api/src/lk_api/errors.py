"""API error envelope: ``{"error": {"code", "message", "details"}}``. No stack traces leak."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lk_rules.errors import RulesError

log = logging.getLogger("lk_api.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def not_found(what: str) -> ApiError:
    # Used for both "missing" and "not yours": never reveal that another user's object exists.
    return ApiError(404, "NOT_FOUND", f"{what} not found")


def _body(code: str, message: str, details: object = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RulesError)
    async def _rules(_: Request, exc: RulesError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=422)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(_body(exc.detail["code"], exc.detail.get("message", "")), status_code=exc.status_code)
        return JSONResponse(_body("HTTP_ERROR", str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(_body("REQUEST_INVALID", "Request validation failed", {"issues": issues}),
                            status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", extra={"path": request.url.path, "exc_type": type(exc).__name__})
        return JSONResponse(_body("INTERNAL_ERROR", "Internal error"), status_code=500)
