from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from sideline.db.models import Base


def make_engine(url: str) -> Engine:
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kw["poolclass"] = StaticPool
    engine = create_engine(url, future=True, **kw)
    if url.startswith("sqlite") and url not in ("sqlite://", "sqlite:///:memory:"):
        # The API and a worker share this file. Write-ahead logging lets a
        # reader proceed while the other writes, and the busy timeout makes
        # a brief overlap a wait rather than an error.
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql("PRAGMA busy_timeout=5000")
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    return engine


def add_missing_columns(engine: Engine) -> list[str]:
    """`create_all` makes tables that do not exist and never touches ones
    that do, so a column added to a model is absent from a database made
    before it and every insert fails. There is no migration tool here, and
    the only schema changes so far add columns; this adds any the models
    have and the database lacks, nullable, and returns their names. A
    column that must not be null is added without that constraint, which
    old rows could not satisfy anyway; where the model gives a plain
    default (a number, a string), old rows take it, so a match made before
    `pitch_length` existed reads 105 and not None."""
    insp = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'))
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if isinstance(default, (int, float, str, bool)):
                    conn.execute(text(f"UPDATE {table.name} SET {col.name} = :v WHERE {col.name} IS NULL"), {"v": default})
                added.append(f"{table.name}.{col.name}")
    return added


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
