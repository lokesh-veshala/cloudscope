from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text


DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://cloudscope:cloudscope@postgres:5432/cloudscope"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def initialize_database() -> None:
    schema = Path(__file__).with_name("schema.sql").read_text()
    statements = [part.strip() for part in schema.split(";") if part.strip()]
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(1435780241)"))
        for statement in statements:
            connection.exec_driver_sql(statement)


def ping_database() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def rows(result) -> list[dict]:
    return [dict(row) for row in result.mappings().all()]
