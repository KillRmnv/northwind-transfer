from fastapi.testclient import TestClient
from pytest_check import check

from app.main import app


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")

    with check:
        assert response.status_code == 200
    with check:
        assert response.json() == {"status": "ok"}
