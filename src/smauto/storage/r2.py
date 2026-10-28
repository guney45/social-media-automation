"""Cloudflare R2 storage (S3-compatible).

R2 is the cheapest way to give Instagram a public URL: 10 GB free and no egress
charges. Set a 30-day lifecycle rule on the bucket — Instagram copies the media
on publish, so keeping it afterwards only burns quota.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any

from smauto.config import get_settings
from smauto.logging import get_logger
from smauto.storage.base import StorageError, content_type_for

log = get_logger(__name__)


class R2Storage:
    name = "r2"

    def __init__(self) -> None:
        settings = get_settings()
        missing = [
            field
            for field in (
                "r2_account_id",
                "r2_access_key_id",
                "r2_secret_access_key",
                "r2_bucket",
                "r2_public_base_url",
            )
            if not getattr(settings, field)
        ]
        if missing:
            raise StorageError("R2 is not configured: " + ", ".join(m.upper() for m in missing))

        self.bucket = settings.r2_bucket
        self.base_url = settings.r2_public_base_url.rstrip("/")
        self._account_id = settings.r2_account_id
        self._key_id = settings.r2_access_key_id
        self._secret = settings.r2_secret_access_key

    @cached_property
    def _client(self) -> Any:
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=f"https://{self._account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=self._key_id,
            aws_secret_access_key=self._secret,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put(self, path: Path, key: str, *, content_type: str | None = None) -> str:
        if not path.exists():
            raise StorageError(f"cannot upload missing file: {path}")
        try:
            self._client.upload_file(
                str(path),
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type or content_type_for(path)},
            )
        except Exception as exc:
            raise StorageError(f"R2 upload failed for {key}: {exc}"[:400]) from exc

        url = self.public_url(key)
        log.info("uploaded to r2", key=key, url=url, bytes=path.stat().st_size)
        return url

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"R2 delete failed for {key}: {exc}"[:400]) from exc

    def public_url(self, key: str) -> str:
        return f"{self.base_url}/{key.lstrip('/')}"

    def supports_public_urls(self) -> bool:
        return True

    def healthcheck(self) -> None:
        """Round-trip a small object. Used by `smauto doctor`."""
        import io

        key = ".smauto-healthcheck"
        try:
            self._client.upload_fileobj(
                io.BytesIO(b"ok"), self.bucket, key, ExtraArgs={"ContentType": "text/plain"}
            )
            self._client.head_object(Bucket=self.bucket, Key=key)
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"R2 healthcheck failed: {exc}"[:400]) from exc


__all__ = ["R2Storage"]
