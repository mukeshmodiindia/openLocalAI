"""Tool functions the agent graph can call. Each wraps a connector and
returns plain text/dicts so they're easy to drop into an LLM tool-call
response. Every tool call is wrapped so that calling a not-yet-configured
integration returns a clear message to the model instead of crashing the
agent loop — this is what lets you deploy with only the LLM running and
wire up ServiceNow/Confluence/DBs whenever you're ready.
"""
from __future__ import annotations

from functools import wraps

from src.config import get_config
from src.connectors.errors import IntegrationNotConfigured
from src.connectors.servicenow import ServiceNowConnector
from src.connectors.mysql_conn import MySQLConnector
from src.connectors.mongo_conn import MongoConnector
from src.connectors.postgres_conn import PostgresConnector
from src.rag.vector_store import VectorStore
from src.rag.web_search import WebLookup


def _graceful(fn):
    """Turns IntegrationNotConfigured into a normal return value the model
    can read and relay to the user, instead of an unhandled exception."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except IntegrationNotConfigured as e:
            return {"error": str(e), "integration": e.integration}
    return wrapper


@_graceful
def search_past_change_tasks(query: str) -> list[dict]:
    """Find similar past change tasks in ServiceNow to use as a template."""
    return ServiceNowConnector().search_similar_change_tasks(query)


@_graceful
def search_confluence_knowledge(query: str) -> list[dict]:
    """RAG search over indexed Confluence runbooks."""
    return VectorStore().search(query, source_filter="confluence")


@_graceful
def lookup_vendor_docs(query: str) -> list[dict]:
    """Live search of official MySQL/MongoDB/PostgreSQL documentation."""
    return WebLookup().search(query)


@_graceful
def mysql_database_size(schema: str) -> float:
    return MySQLConnector().database_size_mb(schema)


@_graceful
def mongo_database_size(db_name: str) -> float:
    return MongoConnector().database_size_mb(db_name)


@_graceful
def postgres_database_size(db_name: str) -> float:
    return PostgresConnector().database_size_mb(db_name)


@_graceful
def mysql_list_users() -> list[dict]:
    return MySQLConnector().list_users()


@_graceful
def mongo_list_users(db_name: str = "admin") -> list[dict]:
    return MongoConnector().list_users(db_name)


# Full registry — includes tools for integrations that may not be enabled.
TOOL_REGISTRY = {
    "search_past_change_tasks": search_past_change_tasks,
    "search_confluence_knowledge": search_confluence_knowledge,
    "lookup_vendor_docs": lookup_vendor_docs,
    "mysql_database_size": mysql_database_size,
    "mongo_database_size": mongo_database_size,
    "postgres_database_size": postgres_database_size,
    "mysql_list_users": mysql_list_users,
    "mongo_list_users": mongo_list_users,
}

# Which config flag gates each tool — used to decide what to actually expose
# to the LLM (see agent/graph.py). Tools not listed here are always exposed
# (there's nothing to gate — e.g. none currently, but keeps this extensible).
TOOL_REQUIRES = {
    "search_past_change_tasks": ("servicenow", "enabled"),
    "search_confluence_knowledge": ("confluence", "enabled"),
    "lookup_vendor_docs": ("web_lookup", "enabled"),
    "mysql_database_size": ("databases", "mysql", "enabled"),
    "mysql_list_users": ("databases", "mysql", "enabled"),
    "mongo_database_size": ("databases", "mongodb", "enabled"),
    "mongo_list_users": ("databases", "mongodb", "enabled"),
    "postgres_database_size": ("databases", "postgres", "enabled"),
}


def _get_nested(d: dict, path: tuple, default=False):
    for key in path:
        if not isinstance(d, dict) or key not in d:
            return default
        d = d[key]
    return d


def enabled_tools() -> dict:
    """Returns only the TOOL_REGISTRY entries whose backing integration is
    currently enabled in config.yaml — so the LLM only ever sees tools it
    can actually use right now. Re-evaluated on every graph rebuild, so
    enabling an integration and calling POST /admin/reload-config (or
    restarting the agent) is enough to pick up new tools with no code change.
    """
    cfg = get_config().raw
    result = {}
    for name, fn in TOOL_REGISTRY.items():
        gate = TOOL_REQUIRES.get(name)
        if gate is None or _get_nested(cfg, gate, False):
            result[name] = fn
    return result

