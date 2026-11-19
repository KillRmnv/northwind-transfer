import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

MASK = "*****"

SECRET_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "x-signature",
    "signature",
    "cookie",
    "private_key",
    "access_token",
    "client_secret",
    "session_id",
)

PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=\-]+"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key|signature|access_token|client_secret|session_id|x-api-key)\b\s*[=:]\s*[^\s,;&\"'}]+"
    ),
    re.compile(
        r"(?i)([\"']?(?:password|passwd|secret|token|api[_-]?key|authorization|x-signature|access_token|client_secret|session_id)[\"']?\s*:\s*[\"'])([^\"']+)"
    ),
    re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"),
    re.compile(
        r"(?i)\b(access_token|client_secret|session_id|x-api-key)\s*[=:]\s*[\"']([^\"']+)[\"']"
    ),
]


def redact_text(value: str) -> str:
    result = value
    result = PATTERNS[0].sub(lambda m: f"{m.group(1)} {MASK}", result)
    result = PATTERNS[1].sub(lambda m: f"{m.group(1)}={MASK}", result)
    result = PATTERNS[2].sub(lambda m: f"{m.group(1)}{MASK}", result)
    result = PATTERNS[3].sub(lambda m: f"{m.group(1)}{MASK}{m.group(2)}", result)
    result = PATTERNS[4].sub(lambda m: f"{m.group(1)}={MASK}", result)
    return result


def redact_value(value):
    if isinstance(value, dict):
        return {
            key: (MASK if _is_secret_key(key) else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _is_secret_key(key: str) -> bool:
    parts = [part for part in re.split(r"[^a-z0-9]+", key.lower()) if part]
    for marker in SECRET_KEYS:
        wanted = [part for part in re.split(r"[^a-z0-9]+", marker) if part]
        for index in range(len(parts) - len(wanted) + 1):
            if parts[index : index + len(wanted)] == wanted:
                return True
    return "api" in parts and "key" in parts


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = redact_value(record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        for key in ("transfer_id", "provider_id", "event_id", "method", "path", "status"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact_value(payload), ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]
