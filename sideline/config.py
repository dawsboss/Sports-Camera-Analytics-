"""Where things live, from the environment.

Defaults run everything against a local directory and SQLite with jobs run
inline, so the skeleton works on a laptop with no services. docker-compose
sets the Postgres, MinIO and Redis variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    db_url: Optional[str] = None
    blob_root: Optional[Path] = None
    minio_endpoint: Optional[str] = None
    minio_access_key: Optional[str] = None
    minio_secret_key: Optional[str] = None
    minio_bucket: str = "sideline"
    minio_secure: bool = False
    redis_url: Optional[str] = None
    inline_jobs: bool = True
    sample_fps: float = 5.0
    cache_frames: bool = True
    pipeline_version: str = "0.1.0"

    @classmethod
    def from_env(cls, data_dir: Optional[Path] = None) -> "Settings":
        env = os.environ.get
        data_dir = data_dir or Path(env("SIDELINE_DATA_DIR", "./data"))
        redis_url = env("SIDELINE_REDIS_URL") or None
        return cls(
            data_dir=data_dir,
            db_url=env("SIDELINE_DB_URL") or None,
            blob_root=Path(env("SIDELINE_BLOB_ROOT")) if env("SIDELINE_BLOB_ROOT") else None,
            minio_endpoint=env("SIDELINE_MINIO_ENDPOINT") or None,
            minio_access_key=env("SIDELINE_MINIO_ACCESS_KEY") or None,
            minio_secret_key=env("SIDELINE_MINIO_SECRET_KEY") or None,
            minio_bucket=env("SIDELINE_MINIO_BUCKET", "sideline"),
            minio_secure=env("SIDELINE_MINIO_SECURE", "0") == "1",
            redis_url=redis_url,
            inline_jobs=env("SIDELINE_INLINE_JOBS", "1" if not redis_url else "0") == "1",
            sample_fps=float(env("SIDELINE_SAMPLE_FPS", "5")),
            cache_frames=env("SIDELINE_CACHE_FRAMES", "1") == "1",
        )

    @property
    def resolved_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.data_dir / 'sideline.db').resolve()}"

    @property
    def workdir(self) -> Path:
        p = self.data_dir / "work"
        p.mkdir(parents=True, exist_ok=True)
        return p


def build_blobs(settings: Settings):
    from sideline.storage.blobs import LocalBlobStore, MinioBlobStore

    if settings.minio_endpoint:
        return MinioBlobStore.connect(
            settings.minio_endpoint, settings.minio_access_key or "", settings.minio_secret_key or "",
            settings.minio_bucket, secure=settings.minio_secure,
        )
    return LocalBlobStore(settings.blob_root or settings.data_dir / "blobs")


_ENGINES: dict[str, object] = {}


def build_engine(settings: Settings):
    """One engine per database URL for the life of the process. Stages, jobs
    and the API all ask for it, and an in-memory SQLite would otherwise be
    a different database each time."""
    from sideline.db.session import make_engine

    url = settings.resolved_db_url
    if url not in _ENGINES:
        _ENGINES[url] = make_engine(url)
    return _ENGINES[url]


def build_queue(settings: Settings):
    from sideline.queue import InlineQueue, RQQueue

    if settings.inline_jobs or not settings.redis_url:
        return InlineQueue(settings)
    return RQQueue(settings.redis_url)
