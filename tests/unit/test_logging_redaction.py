import io
import json
import logging

from app.core.logging import (
    JsonFormatter,
    RedactingFilter,
    configure_logging,
    redact_text,
    redact_value,
)


def capture(record: logging.LogRecord) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    handler.handle(record)
    return stream.getvalue()


def test_redacts_bearer_tokens() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "call with Authorization: Bearer abc.def.ghi", None, None
    )
    payload = json.loads(capture(record))
    assert "abc.def.ghi" not in payload["message"]
    assert "*****" in payload["message"]


def test_redacts_key_value_secrets() -> None:
    text = "password=hunter2 token: abc123 api_key=deadbeef"
    redacted = redact_text(text)
    for secret in ("hunter2", "abc123", "deadbeef"):
        assert secret not in redacted
    assert redacted.count("*****") == 3


def test_redacts_dict_fields() -> None:
    payload = redact_value(
        {
            "client_id": "c_001",
            "provider_webhook_secret": "super-secret",
            "headers": {"X-Signature": "deadbeef", "Accept": "application/json"},
            "items": [{"access_token": "tok"}],
        }
    )
    assert payload["provider_webhook_secret"] == "*****"
    assert payload["headers"]["X-Signature"] == "*****"
    assert payload["items"][0]["access_token"] == "*****"
    assert payload["client_id"] == "c_001"
    assert payload["headers"]["Accept"] == "application/json"


def test_redacts_log_args() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "webhook provider=%s secret=%s",
        ("prov_alpha", "s3cr3t"),
        None,
    )
    payload = json.loads(capture(record))
    assert "s3cr3t" not in payload["message"]
    assert "prov_alpha" in payload["message"]


def test_redacts_signature_header_value() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "headers {'X-Signature': 'abc123', 'Content-Type': 'application/json'}",
        None,
        None,
    )
    payload = json.loads(capture(record))
    assert "abc123" not in payload["message"]
    assert "application/json" in payload["message"]


def test_configure_logging_installs_redacting_handler() -> None:
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert root.handlers
    assert any(isinstance(item, RedactingFilter) for item in root.handlers[0].filters)
