"""Blobs: source video, transcoded video, cached frames, every stage artifact.

MinIO in the homelab, a directory on a laptop and in the tests. Keys are
immutable: a stage that wants to write again writes a new version directory,
so `put` refuses to overwrite unless told otherwise, and nothing in a stage
ever tells it otherwise.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Optional, Protocol


class ImmutableWriteError(RuntimeError):
    pass


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", overwrite: bool = False) -> None: ...
    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream", overwrite: bool = False) -> None: ...
    def get(self, key: str) -> bytes: ...
    def get_to_file(self, key: str, path: Path) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def local_path(self, key: str) -> Optional[Path]: ...


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f"key escapes the store: {key}")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", overwrite: bool = False) -> None:
        p = self._path(key)
        if p.exists() and not overwrite:
            raise ImmutableWriteError(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream", overwrite: bool = False) -> None:
        p = self._path(key)
        if p.exists() and not overwrite:
            raise ImmutableWriteError(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        shutil.copyfile(path, tmp)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def get_to_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), path)
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.exists():
            return []
        out = []
        for p in base.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                out.append(p.relative_to(self.root).as_posix())
        return sorted(out)

    def local_path(self, key: str) -> Optional[Path]:
        p = self._path(key)
        return p if p.is_file() else None


class MinioBlobStore:
    def __init__(self, client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    @classmethod
    def connect(cls, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False) -> "MinioBlobStore":
        from minio import Minio

        return cls(Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure), bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", overwrite: bool = False) -> None:
        if not overwrite and self.exists(key):
            raise ImmutableWriteError(key)
        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data), content_type=content_type)

    def put_file(self, key: str, path: Path, content_type: str = "application/octet-stream", overwrite: bool = False) -> None:
        if not overwrite and self.exists(key):
            raise ImmutableWriteError(key)
        self.client.fput_object(self.bucket, key, str(path), content_type=content_type)

    def get(self, key: str) -> bytes:
        resp = self.client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def get_to_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.fget_object(self.bucket, key, str(path))
        return path

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self.client.stat_object(self.bucket, key)
            return True
        except S3Error as e:
            if e.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise

    def list(self, prefix: str) -> list[str]:
        return sorted(o.object_name for o in self.client.list_objects(self.bucket, prefix=prefix, recursive=True))

    def local_path(self, key: str) -> Optional[Path]:
        return None
