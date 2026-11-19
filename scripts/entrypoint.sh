#!/bin/sh
set -e
attempt=0
until uv run --frozen alembic upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 5 ]; then
    echo "migrations failed after 5 attempts" >&2
    exit 1
  fi
  echo "migration attempt $attempt failed, retrying in 3s..." >&2
  sleep 3
done
uv run --frozen python -m app.db.seed
exec uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log
