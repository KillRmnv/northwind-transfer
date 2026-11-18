#!/bin/sh
set -e
uv run --frozen alembic upgrade head
uv run --frozen python -m app.db.seed
exec uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log
