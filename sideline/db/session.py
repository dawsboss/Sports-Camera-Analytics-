from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
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
    Base.metadata.create_all(engine)
    return engine


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
