from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import DomainError, ValidationError


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


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
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
    )
