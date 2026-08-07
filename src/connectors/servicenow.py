"""ServiceNow REST Table API connector — read access for change/incident/task
tables. Used as the source of truth for repeatable change-task templates.
"""
from __future__ import annotations

import requests

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured


class ServiceNowConnector:
    def __init__(self):
        sn = get_config().raw.get("servicenow", {})
        self.enabled = sn.get("enabled", False)
        if not self.enabled:
            raise IntegrationNotConfigured("ServiceNow")

        self.base_url = sn["instance_url"].rstrip("/")
        self.tables = sn["tables"]
        self.lookback_days = sn.get("lookback_days", 180)

        auth = sn["auth"]
        if auth["type"] == "basic":
            self.session = requests.Session()
            self.session.auth = (auth["username"], auth["password"])
        elif auth["type"] == "oauth2":
            self.session = requests.Session()
            self.session.headers["Authorization"] = f"Bearer {self._get_oauth_token(auth)}"
        else:
            raise ValueError(f"Unsupported ServiceNow auth type: {auth['type']}")

        self.session.headers.update({"Accept": "application/json"})

    def _get_oauth_token(self, auth: dict) -> str:
        resp = requests.post(
            auth["token_url"],
            data={
                "grant_type": "client_credentials",
                "client_id": auth["client_id"],
                "client_secret": auth["client_secret"],
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def search_similar_change_tasks(self, query: str, limit: int = 10) -> list[dict]:
        """Free-text search over short_description for past change requests.
        Good enough as a starting point — swap for a proper text-index query
        against your instance if you need better recall.
        """
        table = self.tables["change_request"]
        params = {
            "sysparm_query": f"short_descriptionLIKE{query}^ORdescriptionLIKE{query}",
            "sysparm_limit": limit,
            "sysparm_fields": "number,short_description,description,state,close_notes,sys_updated_on",
        }
        resp = self.session.get(f"{self.base_url}/api/now/table/{table}", params=params)
        resp.raise_for_status()
        return resp.json().get("result", [])

    def get_change_task(self, number: str) -> dict | None:
        table = self.tables["change_request"]
        params = {"sysparm_query": f"number={number}", "sysparm_limit": 1}
        resp = self.session.get(f"{self.base_url}/api/now/table/{table}", params=params)
        resp.raise_for_status()
        results = resp.json().get("result", [])
        return results[0] if results else None

    def create_change_task_draft(self, short_description: str, description: str) -> dict:
        """Creates a change_request record in draft/new state for human review
        — the agent should never move a change task past 'new' on its own.
        """
        table = self.tables["change_request"]
        payload = {
            "short_description": short_description,
            "description": description,
            "state": "-5",  # New, in most out-of-box SN workflows
        }
        resp = self.session.post(f"{self.base_url}/api/now/table/{table}", json=payload)
        resp.raise_for_status()
        return resp.json()["result"]
