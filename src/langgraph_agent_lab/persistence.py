"""Checkpointer adapter."""

from __future__ import annotations

import sqlite3
from importlib import import_module

_OPEN_SQLITE_CONNECTIONS: list[sqlite3.Connection] = []


def _normalize_sqlite_path(database_url: str | None) -> str:
    if not database_url:
        return "checkpoints.db"
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    return database_url


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> object | None:
    if kind == "none":
        return None

    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError(
                "SQLite checkpointer requires: pip install langgraph-checkpoint-sqlite"
            ) from exc

        db_path = _normalize_sqlite_path(database_url)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.commit()

        saver = SqliteSaver(conn=conn)
        _OPEN_SQLITE_CONNECTIONS.append(conn)
        return saver

    if kind == "postgres":
        try:
            postgres_module = import_module("langgraph.checkpoint.postgres")
            postgres_saver_cls = postgres_module.PostgresSaver
        except Exception as exc:
            raise RuntimeError(
                "Postgres checkpointer requires: pip install langgraph-checkpoint-postgres"
            ) from exc
        return postgres_saver_cls.from_conn_string(database_url or "")

    raise ValueError(f"Unknown checkpointer kind: {kind}")
