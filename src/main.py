"""FastAPI entrypoint. Exposes a simple /plan endpoint that Open WebUI (or
curl, or the Slack bot) can call. Run with:

    uvicorn src.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel

from src.config import get_config, reload_config
from src.agent.graph import run_plan_request, reset_agent
from src.store import Store

cfg = get_config()
logging.basicConfig(level=cfg.raw.get("agent", {}).get("log_level", "INFO"))
log = logging.getLogger("openlocalai")

app = FastAPI(title="openLocalAI")


class PlanRequest(BaseModel):
    request: str
    post_to_slack: bool = False


class AdhocDBQuery(BaseModel):
    provider: str            # mysql | mongodb | postgres
    connection: dict         # e.g. {"host": "...", "user": "...", "password": "...", "port": 3306}
    operation: str           # database_size | list_users | raw_query
    params: dict = {}        # e.g. {"schema": "orders"} or {"sql": "SELECT ..."}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/integrations")
def integrations():
    """Shows what's currently enabled, so you can deploy with just the LLM
    running and check what still needs to be configured."""
    raw = get_config().raw
    dbs = raw.get("databases", {})
    return {
        "servicenow": raw.get("servicenow", {}).get("enabled", False),
        "confluence": raw.get("confluence", {}).get("enabled", False),
        "slack": raw.get("slack", {}).get("enabled", False),
        "web_lookup": raw.get("web_lookup", {}).get("enabled", False),
        "databases": {
            "mysql": dbs.get("mysql", {}).get("enabled", False),
            "mongodb": dbs.get("mongodb", {}).get("enabled", False),
            "postgres": dbs.get("postgres", {}).get("enabled", False),
        },
    }


@app.post("/admin/reload-config")
def reload_config_endpoint():
    """Re-reads config.yaml from disk and rebuilds the agent's tool set —
    call this after editing config.yaml to enable ServiceNow/Confluence/a
    DB/Slack. No container restart needed."""
    reload_config()
    reset_agent()
    return {"status": "reloaded", "integrations": integrations()}


@app.post("/plan")
def create_plan(body: PlanRequest):
    plan_text = run_plan_request(body.request)
    plan_id = Store().save_plan(body.request, plan_text, sources=[])

    if body.post_to_slack and cfg.raw.get("slack", {}).get("enabled"):
        from src.connectors.slack_conn import SlackConnector
        SlackConnector().post_plan_for_review(plan_text)
        Store().mark_posted(plan_id, slack_ts="")  # filled in once Slack ack is wired to the button handler

    return {"plan_id": plan_id, "plan": plan_text}


@app.get("/history")
def history(limit: int = 20):
    return {"plans": Store().list_recent(limit)}


@app.post("/db/adhoc-query")
def adhoc_db_query(body: AdhocDBQuery):
    """Run a one-off database task against a connection you supply in the
    request — no need to add it to config.yaml first. Useful for a
    single task against a DB you don't want to pre-register."""
    if body.provider == "mysql":
        from src.connectors.mysql_conn import MySQLConnector
        conn = MySQLConnector(conn_override=body.connection)
        if body.operation == "database_size":
            return {"size_mb": conn.database_size_mb(body.params["schema"])}
        if body.operation == "list_users":
            return {"users": conn.list_users()}
        if body.operation == "raw_query":
            return {"rows": conn.run_query(body.params["sql"])}

    elif body.provider == "mongodb":
        from src.connectors.mongo_conn import MongoConnector
        conn = MongoConnector(conn_override=body.connection)
        if body.operation == "database_size":
            return {"size_mb": conn.database_size_mb(body.params["db_name"])}
        if body.operation == "list_users":
            return {"users": conn.list_users(body.params.get("db_name", "admin"))}

    elif body.provider == "postgres":
        from src.connectors.postgres_conn import PostgresConnector
        conn = PostgresConnector(conn_override=body.connection)
        if body.operation == "database_size":
            return {"size_mb": conn.database_size_mb(body.params["db_name"])}
        if body.operation == "raw_query":
            return {"rows": conn.run_query(body.params["sql"])}

    else:
        return {"error": f"Unknown provider: {body.provider}"}

    return {"error": f"Unknown operation '{body.operation}' for provider '{body.provider}'"}


if __name__ == "__main__":
    import uvicorn
    agent_cfg = cfg.raw.get("agent", {})
    uvicorn.run(
        "src.main:app",
        host=agent_cfg.get("host", "0.0.0.0"),
        port=agent_cfg.get("port", 8000),
        reload=False,
    )
