"""Read-only PostgreSQL connector, mirrors mysql_conn.py."""
from __future__ import annotations

import psycopg2
import psycopg2.extras

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured

_READ_ONLY_PREFIXES = ("SELECT", "SHOW", "EXPLAIN")


class PostgresConnector:
    def __init__(self, conn_override: dict | None = None):
        if conn_override is not None:
            self.enabled = True
            self.readonly = conn_override.get("readonly", True)
            self._conn_kwargs = dict(
                host=conn_override["host"], port=conn_override.get("port", 5432),
                user=conn_override["user"], password=conn_override["password"],
                dbname=conn_override.get("database", "postgres"),
            )
            return

        db = get_config().raw.get("databases", {}).get("postgres", {})
        self.enabled = db.get("enabled", False)
        if not self.enabled:
            raise IntegrationNotConfigured(
                "PostgreSQL",
                "Set databases.postgres.enabled: true and connection "
                "details in config.yaml, or pass a one-off connection via "
                "the /db/adhoc-query endpoint without changing config.yaml at all."
            )
        self.readonly = db.get("readonly", True)
        self._conn_kwargs = dict(
            host=db["host"], port=db.get("port", 5432),
            user=db["user"], password=db["password"],
            dbname=db.get("database", "postgres"),
        )

    def _connect(self):
        return psycopg2.connect(**self._conn_kwargs)

    def run_query(self, sql: str) -> list[dict]:
        if self.readonly and not sql.strip().upper().startswith(_READ_ONLY_PREFIXES):
            raise PermissionError(
                "Postgres connector is in read-only mode; only SELECT/SHOW/"
                "EXPLAIN statements are allowed."
            )
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql)
            return cur.fetchall()
        finally:
            conn.close()

    def database_size_mb(self, db_name: str) -> float:
        conn = self._connect()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT pg_database_size(%s) / 1024.0 / 1024.0 AS size_mb", (db_name,))
            row = cur.fetchone()
            return round(row["size_mb"], 2) if row else 0.0
        finally:
            conn.close()
