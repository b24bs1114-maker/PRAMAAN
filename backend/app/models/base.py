"""SQLite engine, session factory and declarative base.

SQLite is a deliberate architectural choice, not a placeholder: a single
portable file per deployment keeps the whole platform offline and on-premise.
Foreign keys are enforced per connection (SQLite disables them by default) and
WAL journalling is enabled so reads do not block the writer.

Transactions are ``BEGIN IMMEDIATE``, not SQLite's default ``BEGIN DEFERRED``.
That is a correctness requirement of the hash-chained audit log, not a
performance tweak: appending a row means *reading* the current chain head and
then *writing* a row that commits to it. Under DEFERRED, two concurrent requests
both read the same head before either takes the write lock, both build a row on
it, and the chain forks -- one row's ``previous_hash`` no longer matches the
preceding row's ``row_hash``, which verification correctly reports as tampering.
IMMEDIATE takes the write lock up front, so the read-then-append pair is
serialised by the database and the second writer sees the first writer's row.
``busy_timeout`` makes the loser wait instead of failing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Column, create_engine, event
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings

logger = logging.getLogger("pramaan.db")


class Base(DeclarativeBase):
    """Declarative base for all PRAMAAN tables."""


#: How long a writer waits for the write lock before giving up. Appends are
#: milliseconds long, so anything that waits longer than this is a stuck
#: transaction rather than ordinary contention.
BUSY_TIMEOUT_MS = 10_000


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enforce referential integrity, use WAL journalling, and wait for locks.

    ``isolation_level = None`` hands transaction control to SQLAlchemy so the
    ``begin`` handler below can choose IMMEDIATE; pysqlite would otherwise emit
    its own implicit DEFERRED ``BEGIN`` and there would be no transaction left to
    start.
    """
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


@event.listens_for(Engine, "begin")
def _sqlite_begin_immediate(connection: Connection) -> None:
    """Start every transaction as a writer.

    Single-writer semantics for the whole request. The alternative -- promoting
    only the audit append to a write lock -- cannot work, because by the time
    ``audit.record()`` runs the request's DEFERRED transaction has already begun
    and SQLite has no statement that upgrades a read transaction to a write one
    without re-reading. Serialising at ``begin`` is what makes the chain append
    atomic with respect to other requests.
    """
    connection.exec_driver_sql("BEGIN IMMEDIATE")


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the process-wide SQLite engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            settings.database_url,
            echo=settings.db_echo,
            future=True,
            # FastAPI serves requests from a threadpool; sessions are per-request.
            connect_args={"check_same_thread": False},
        )
        logger.info("SQLite engine bound to %s", settings.db_path)
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False
        )
    return _session_factory


def _existing_columns(connection: Connection, table: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _sqlite_type(column: Column[Any]) -> str:
    """Best-effort DDL type for an added column."""
    try:
        return column.type.compile(dialect=sqlite.dialect())
    except Exception:  # noqa: BLE001 - fall back to SQLite's permissive default
        return "TEXT"


def _default_literal(column: Column[Any]) -> str | None:
    """SQL literal for a column's declared scalar default, if it has one.

    Callable defaults (``utcnow``) and server-side defaults are skipped: only a
    plain scalar the model itself declares is carried into the DDL.
    """
    default = getattr(column.default, "arg", None)
    if callable(default) or default is None:
        return None
    if isinstance(default, bool):
        return str(int(default))
    if isinstance(default, (int, float)):
        return str(default)
    if isinstance(default, str):
        escaped = default.replace("'", "''")
        return f"'{escaped}'"
    return None


def migrate_schema(settings: Settings | None = None) -> list[str]:
    """Add columns that the models declare but an existing database lacks.

    ``create_all`` creates missing *tables*; it never alters a table that already
    exists. A case file created by an earlier build therefore keeps the old
    columns, and every query touching a newer one fails with
    ``no such column``. This closes that gap the only way SQLite allows -- an
    additive ``ALTER TABLE ... ADD COLUMN``.

    Deliberately additive only: nothing is dropped, renamed, re-typed or
    back-filled with invented values. A column that has a declared default gets
    that default so pre-existing rows read the same as a newly created row would
    (an unset ``priority`` reads ``medium`` because that is what "not set" means
    here, not because a priority was assigned). Anything else -- a removed
    column, a changed type, a new constraint -- is left alone and reported, since
    resolving it needs a decision this function cannot make.

    Returns the list of ``table.column`` additions performed.
    """
    from app import models  # noqa: F401  (import registers the mappers)

    engine = get_engine(settings)
    if engine.dialect.name != "sqlite":  # pragma: no cover - SQLite is the only target
        return []

    added: list[str] = []
    with engine.begin() as connection:
        present_tables = {
            str(row[0])
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in Base.metadata.sorted_tables:
            if table.name not in present_tables:
                continue  # create_all will make it, with every column
            existing = _existing_columns(connection, table.name)
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.default is None:
                    # SQLite cannot add a NOT NULL column without a default, and
                    # inventing one would put fabricated data in the case file.
                    logger.error(
                        "Cannot add required column %s.%s automatically; the "
                        "database needs a manual migration.",
                        table.name,
                        column.name,
                    )
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {_sqlite_type(column)}"
                literal = _default_literal(column)
                if literal is not None:
                    ddl += f" DEFAULT {literal}"
                connection.exec_driver_sql(ddl)
                added.append(f"{table.name}.{column.name}")
                logger.warning(
                    "Schema migration: added missing column %s.%s", table.name, column.name
                )
    return added


def init_db(settings: Settings | None = None) -> None:
    """Create all tables if they do not exist, then apply additive migrations."""
    from app import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(bind=get_engine(settings))
    added = migrate_schema(settings)
    logger.info(
        "Database schema ready (%d tables%s)",
        len(Base.metadata.tables),
        f", {len(added)} column(s) added" if added else "",
    )


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional scope for scripts and background work."""
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/session factory (used by tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
