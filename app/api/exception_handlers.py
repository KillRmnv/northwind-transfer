from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import DomainError, ValidationError
from app.core.logging import request_id_var


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "type": "about:blank",
            "title": exc.code,
            "status": exc.status,
            "detail": exc.message,
            "instance": str(request.url.path),
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details or {},
            },
        },
        media_type="application/problem+json",
    )


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = {".".join(str(part) for part in error["loc"]): error["msg"] for error in exc.errors()}
    return await domain_error_handler(
        request, ValidationError("request validation failed", details=details)
    )


async def pydantic_validation_handler(
    request: Request, exc: PydanticValidationError
) -> JSONResponse:
    details = {".".join(str(part) for part in error["loc"]): error["msg"] for error in exc.errors()}
    return await domain_error_handler(
        request, ValidationError("request validation failed", details=details)
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    from app.core.exceptions import MethodNotAllowedError, NotFoundError

    if exc.status_code == 404:
        mapped: DomainError = NotFoundError(str(exc.detail))
    elif exc.status_code == 405:
        mapped = MethodNotAllowedError(str(exc.detail))
    else:
        mapped = ValidationError(str(exc.detail))
        mapped.status = exc.status_code
        mapped.code = "HTTP_ERROR"
    return await domain_error_handler(request, mapped)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or request_id_var.get() or "-"
    return JSONResponse(
        status_code=500,
        content={
            "type": "about:blank",
            "title": "INTERNAL_ERROR",
            "status": 500,
            "detail": "internal error",
            "instance": str(request.url.path),
            "error": {"code": "INTERNAL_ERROR", "message": "internal error", "details": {}},
        },
        media_type="application/problem+json",
        headers={"X-Request-ID": request_id},
    )
