"""Storage adapter supporting local filesystem and S3/Cloudflare R2 object storage."""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class StorageBackend:
    def save(self, relative_path: str, content: bytes) -> str:
        raise NotImplementedError

    def read(self, relative_path: str) -> bytes | None:
        raise NotImplementedError

    def delete(self, relative_path: str) -> bool:
        raise NotImplementedError

    def exists(self, relative_path: str) -> bool:
        raise NotImplementedError

    def get_local_path(self, relative_path: str) -> Path | None:
        return None


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str | Path | None = None) -> None:
        if root is None or str(root) == "/app/data/documents":
            root = os.environ.get("UPLOAD_ROOT") or getattr(get_settings(), "upload_root", "data/documents")
            if str(root) == "/app/data/documents":
                root = "data/documents"
        target = Path(root)
        if not target.is_absolute():
            target = (Path.cwd() / target).resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
            self.root = target.resolve()
        except (PermissionError, OSError):
            import tempfile
            fallback = (Path(tempfile.gettempdir()) / "svc_documents").resolve()
            fallback.mkdir(parents=True, exist_ok=True)
            self.root = fallback

    def _resolve(self, relative_path: str) -> Path | None:
        try:
            target = (self.root / relative_path).resolve()
            if self.root not in target.parents and target != self.root:
                return None
            return target
        except (OSError, ValueError):
            return None

    def save(self, relative_path: str, content: bytes) -> str:
        target = self._resolve(relative_path)
        if target is None:
            raise ValueError(f"Path traversal detected: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return str(target)

    def read(self, relative_path: str) -> bytes | None:
        target = self._resolve(relative_path)
        if target and target.is_file():
            return target.read_bytes()
        return None

    def delete(self, relative_path: str) -> bool:
        target = self._resolve(relative_path)
        if target and target.is_file():
            target.unlink()
            return True
        return False

    def exists(self, relative_path: str) -> bool:
        target = self._resolve(relative_path)
        return target is not None and target.is_file()

    def get_local_path(self, relative_path: str) -> Path | None:
        target = self._resolve(relative_path)
        if target and target.is_file():
            return target
        return None


class S3R2StorageBackend(StorageBackend):
    """S3-compatible storage backend (e.g., Cloudflare R2, MinIO, AWS S3)."""

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, bucket: str, region: str = "auto") -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        try:
            import boto3
            self.client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
        except ImportError:
            logger.warning("boto3 is not installed. S3/R2 storage disabled.")
            self.client = None

    def save(self, relative_path: str, content: bytes) -> str:
        if not self.client:
            raise RuntimeError("boto3 client not initialized")
        key = relative_path.lstrip("/")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return f"s3://{self.bucket}/{key}"

    def read(self, relative_path: str) -> bytes | None:
        if not self.client:
            return None
        key = relative_path.lstrip("/")
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except Exception as exc:
            logger.warning("Could not read S3 key %s: %s", key, exc)
            return None

    def delete(self, relative_path: str) -> bool:
        if not self.client:
            return False
        key = relative_path.lstrip("/")
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def exists(self, relative_path: str) -> bool:
        if not self.client:
            return False
        key = relative_path.lstrip("/")
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is not None:
        return _storage

    settings = get_settings()
    s3_endpoint = getattr(settings, "s3_endpoint_url", None)
    s3_access = getattr(settings, "s3_access_key_id", None)
    s3_secret = getattr(settings, "s3_secret_access_key", None)
    s3_bucket = getattr(settings, "s3_bucket", None)

    secret_val = s3_secret.get_secret_value() if s3_secret else None

    if s3_endpoint and s3_access and secret_val and s3_bucket:
        _storage = S3R2StorageBackend(
            endpoint_url=s3_endpoint,
            access_key=s3_access,
            secret_key=secret_val,
            bucket=s3_bucket,
            region=getattr(settings, "s3_region", "auto"),
        )
    else:
        upload_root = os.environ.get("UPLOAD_ROOT") or getattr(settings, "upload_root", "data/documents")
        if upload_root == "/app/data/documents":
            upload_root = "data/documents"
        _storage = LocalStorageBackend(root=upload_root)

    return _storage
