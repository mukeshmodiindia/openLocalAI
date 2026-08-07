"""Read-only MySQL connector for ad-hoc operational questions
(current DB size, user list, etc). Enforces read-only at the query level as
a safety net on top of a least-privilege DB account."""
from __future__ import annotations

import mysql.connector

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured

_READ_ONLY_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "EXPLAIN")


class MySQLConnector:
    def __init__(self, conn_override: dict | None = None):
        if conn_override is not None:
            # Ad-hoc mode: connection details passed in per-request, nothing
            # read from config.yaml at all. Used by POST /db/adhoc-query.
            self.enabled = True
            self.readonly = conn_override.get("readonly", True)
            self._conn_kwargs = dict(
                host=conn_override["host"], port=conn_override.get("port", 3306),
                user=conn_override["user"], password=conn_override["password"],
            )
            return

        db = get_config().raw.get("databases", {}).get("mysql", {})
        self.enabled = db.get("enabled", False)
        if not self.enabled:
            raise IntegrationNotConfigured(
                "MySQL",
                "Set databases.mysql.enabled: true and connection details "
                "in config.yaml, or pass a one-off connection via the "
                "/db/adhoc-query endpoint without changing config.yaml at all."
            )
        self._conn_kwargs = dict(
            host=db["host"], port=db.get("port", 3306),
            user=db["user"], password=db["password"],
        )
        self.readonly = db.get("readonly", True)

    def _connect(self):
        return mysql.connector.connect(**self._conn_kwargs)

    def run_query(self, sql: str) -> list[dict]:
        if self.readonly and not sql.strip().upper().startswith(_READ_ONLY_PREFIXES):
            raise PermissionError(
                "MySQL connector is in read-only mode; only SELECT/SHOW/"
                "DESCRIBE/EXPLAIN statements are allowed."
            )
        conn = self._connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(sql)
            return cur.fetchall()
        finally:
            conn.close()

    def database_size_mb(self, schema: str) -> float:
        # Validate rather than string-format the schema name directly into SQL.
        if not schema.replace("_", "").isalnum():
            raise ValueError(f"Invalid schema name: {schema!r}")
        conn = self._connect()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS size_mb "
                "FROM information_schema.tables WHERE table_schema = %s",
                (schema,),
            )
            rows = cur.fetchall()
            return rows[0]["size_mb"] if rows and rows[0]["size_mb"] is not None else 0.0
        finally:
            conn.close()

    def list_users(self) -> list[dict]:
        return self.run_query("SELECT user, host FROM mysql.user")
