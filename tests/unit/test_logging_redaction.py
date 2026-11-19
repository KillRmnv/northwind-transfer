import io
import json
import logging

from pytest_check import check

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
    with check:
        assert "abc.def.ghi" not in payload["message"]
    with check:
        assert "*****" in payload["message"]


def test_redacts_key_value_secrets() -> None:
    text = "password=hunter2 token: abc123 api_key=deadbeef"
    redacted = redact_text(text)
    for secret in ("hunter2", "abc123", "deadbeef"):
        with check:
            assert secret not in redacted
    with check:
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
    with check:
        assert payload["provider_webhook_secret"] == "*****"
    with check:
        assert payload["headers"]["X-Signature"] == "*****"
    with check:
        assert payload["items"][0]["access_token"] == "*****"
    with check:
        assert payload["client_id"] == "c_001"
    with check:
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
    with check:
        assert "s3cr3t" not in payload["message"]
    with check:
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
    with check:
        assert "abc123" not in payload["message"]
    with check:
        assert "application/json" in payload["message"]


def test_configure_logging_installs_redacting_handler() -> None:
    configure_logging("DEBUG")
    root = logging.getLogger()
    with check:
        assert root.handlers
    with check:
        assert any(isinstance(item, RedactingFilter) for item in root.handlers[0].filters)


def test_redacts_dsn_and_new_secret_names() -> None:
    text = (
        "dsn postgresql://u:hunter2@host:5432/db "
        "access_token=abc client_secret='xyz' session_id: s1"
    )
    with check:
        assert "hunter2" not in redact_text(text)
    with check:
        assert redact_text(text).count("*****") == 4


def test_new_secret_key_matching_does_not_hide_client_id() -> None:
    payload = redact_value(
        {"X-Api-Key": "s", "tokenizer": "ok", "client_id": "c", "session_id": "sid"}
    )
    with check:
        assert payload["X-Api-Key"] == "*****"
    with check:
        assert payload["tokenizer"] == "ok"
    with check:
        assert payload["client_id"] == "c"
    with check:
        assert payload["session_id"] == "*****"
