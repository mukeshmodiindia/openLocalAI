"""The agent's own state: every generated plan, its source, and any Slack
approve/reject decision. Distinct from the MySQL/MongoDB/PostgreSQL
connectors in src/connectors/, which are external data SOURCES the agent
reads from — this module is where the agent writes ITS OWN history.

Defaults to SQLite (zero extra infra, fine for single-node or a single
orchestration replica). Switch internal_store.provider to "postgres" in
config.yaml once you're running more than one orchestration node, so all
replicas share one history/audit trail instead of each keeping its own.
"""
from __future__ import annotations

import json
import sqlite3
import datetime
from pathlib import Path
from contextlib import contextmanager

from src.config import get_config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    request_text TEXT NOT NULL,
    plan_text TEXT NOT NULL,
    sources TEXT,              -- JSON list of {type, ref} e.g. SN number, Confluence page
    status TEXT NOT NULL DEFAULT 'generated',   -- generated | posted | approved | rejected
    slack_ts TEXT,              -- Slack message timestamp, for correlating button clicks
    approved_by TEXT,
    decided_at TEXT
);
"""


class Store:
    def __init__(self):
        cfg = get_config().raw["internal_store"]
        self.provider = cfg.get("provider", "sqlite")
        if self.provider == "sqlite":
            self.path = Path(cfg["sqlite_path"])
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif self.provider == "postgres":
            pg = cfg["postgres"]
            self._pg_kwargs = dict(
                host=pg["host"], port=pg.get("port", 5432),
                user=pg["user"], password=pg["password"], dbname=pg["database"],
            )
        else:
            raise ValueError(f"Unsupported internal_store.provider: {self.provider}")
        self._init_schema()

    @contextmanager
    def _connect(self):
        if self.provider == "sqlite":
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
        else:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(**self._pg_kwargs)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            if self.provider == "sqlite":
                conn.executescript(_SCHEMA)
            else:
                # Postgres uses SERIAL instead of AUTOINCREMENT
                pg_schema = _SCHEMA.replace(
                    "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
                )
                cur = conn.cursor()
                cur.execute(pg_schema)

    def save_plan(self, request_text: str, plan_text: str, sources: list[dict]) -> int:
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            if self.provider == "sqlite":
                cur = conn.execute(
                    "INSERT INTO plans (created_at, request_text, plan_text, sources, status) "
                    "VALUES (?, ?, ?, ?, 'generated')",
                    (now, request_text, plan_text, json.dumps(sources)),
                )
                return cur.lastrowid
            else:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO plans (created_at, request_text, plan_text, sources, status) "
                    "VALUES (%s, %s, %s, %s, 'generated') RETURNING id",
                    (now, request_text, plan_text, json.dumps(sources)),
                )
                return cur.fetchone()[0]

    def mark_posted(self, plan_id: int, slack_ts: str):
        self._update(plan_id, status="posted", slack_ts=slack_ts)

    def mark_decision(self, plan_id: int, approved: bool, decided_by: str):
        self._update(
            plan_id,
            status="approved" if approved else "rejected",
            approved_by=decided_by,
            decided_at=datetime.datetime.utcnow().isoformat(),
        )

    def _update(self, plan_id: int, **fields):
        set_clause = ", ".join(f"{k} = {'?' if self.provider == 'sqlite' else '%s'}" for k in fields)
        placeholder = "?" if self.provider == "sqlite" else "%s"
        with self._connect() as conn:
            cur = conn if self.provider == "sqlite" else conn.cursor()
            query = f"UPDATE plans SET {set_clause} WHERE id = {placeholder}"
            cur.execute(query, (*fields.values(), plan_id))

    def list_recent(self, limit: int = 20) -> list[dict]:
        placeholder = "?" if self.provider == "sqlite" else "%s"
        with self._connect() as conn:
            cur = conn if self.provider == "sqlite" else conn.cursor()
            cur.execute(
                f"SELECT * FROM plans ORDER BY id DESC LIMIT {placeholder}", (limit,)
            )
            rows = cur.fetchall()
            if self.provider == "sqlite":
                return [dict(r) for r in rows]
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
