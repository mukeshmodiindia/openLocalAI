"""Read-only-oriented MongoDB connector for ad-hoc operational questions."""
from __future__ import annotations

from pymongo import MongoClient

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured


class MongoConnector:
    def __init__(self, conn_override: dict | None = None):
        if conn_override is not None:
            self.enabled = True
            self.readonly = conn_override.get("readonly", True)
            self.client = MongoClient(conn_override["uri"])
            return

        db = get_config().raw.get("databases", {}).get("mongodb", {})
        self.enabled = db.get("enabled", False)
        if not self.enabled:
            raise IntegrationNotConfigured(
                "MongoDB",
                "Set databases.mongodb.enabled: true and a uri in "
                "config.yaml, or pass a one-off connection via the "
                "/db/adhoc-query endpoint without changing config.yaml at all."
            )
        self.readonly = db.get("readonly", True)
        self.client = MongoClient(db["uri"])

    def database_size_mb(self, db_name: str) -> float:
        stats = self.client[db_name].command("dbStats")
        return round(stats["storageSize"] / (1024 * 1024), 2)

    def list_databases(self) -> list[dict]:
        return self.client.list_database_names()

    def list_users(self, db_name: str = "admin") -> list[dict]:
        result = self.client[db_name].command("usersInfo")
        return result.get("users", [])

    def create_user(self, db_name: str, username: str, password: str, roles: list[str]):
        if self.readonly:
            raise PermissionError(
                "MongoConnector is configured read-only; set "
                "databases.mongodb.readonly: false in config.yaml to allow "
                "user-creation operations, and use a dedicated admin "
                "credential with the minimum required role."
            )
        return self.client[db_name].command(
            "createUser", username, pwd=password, roles=roles
        )
