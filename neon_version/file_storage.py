"""Private S3-compatible object storage used by neon_version uploads."""
from __future__ import annotations

import os
import re
import threading
from uuid import uuid4

from fastapi import HTTPException


class StorageConfigurationError(RuntimeError):
    """Required Neon Storage settings are missing or invalid."""


class StorageOperationError(RuntimeError):
    """An object storage operation failed without exposing credentials."""


_CLIENT_CACHE = {}
_CLIENT_LOCK = threading.Lock()


def object_key(category: str, owner: object, filename: str) -> str:
    if category not in {"profile", "mentor-profile", "study-pdfs", "study-images"}:
        raise ValueError("Unsupported upload category")
    safe_owner = re.sub(r"[^A-Za-z0-9_-]", "_", str(owner))[:80] or "unknown"
    suffix = filename.rpartition(".")[2].lower()
    if suffix not in {"webp", "jpg", "jpeg", "png", "pdf"}:
        raise ValueError("Unsupported upload extension")
    return f"{category}/{safe_owner}/{uuid4().hex}.{suffix}"


def valid_key(key: str, category: str, suffixes: set[str]) -> bool:
    if not isinstance(key, str) or "\\" in key or ".." in key:
        return False
    parts = key.split("/")
    return (len(parts) == 3 and parts[0] == category and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", parts[1]))
            and bool(re.fullmatch(r"[a-f0-9]{32}\.(?:" + "|".join(re.escape(s) for s in suffixes) + r")", parts[2])))


def _settings():
    names = ("AWS_ENDPOINT_URL_S3", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "STORAGE_BUCKET")
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise StorageConfigurationError("Neon Storageの環境変数が不足しています: " + ", ".join(missing))
    return {name: os.environ[name].strip() for name in names}


def _client():
    settings = _settings()
    cache_key = tuple(settings[name] for name in ("AWS_ENDPOINT_URL_S3", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"))
    with _CLIENT_LOCK:
        if cache_key in _CLIENT_CACHE:
            return _CLIENT_CACHE[cache_key]
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "s3", endpoint_url=settings["AWS_ENDPOINT_URL_S3"],
            aws_access_key_id=settings["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=settings["AWS_SECRET_ACCESS_KEY"],
            region_name=settings["AWS_REGION"], config=Config(signature_version="s3v4"),
        )
        with _CLIENT_LOCK:
            _CLIENT_CACHE[cache_key] = client
        return client
    except StorageConfigurationError:
        raise
    except Exception as exc:
        raise StorageOperationError("Neon Storageクライアントを初期化できませんでした。") from exc


def upload(key: str, content: bytes, content_type: str) -> None:
    try:
        _client().put_object(Bucket=_settings()["STORAGE_BUCKET"], Key=key,
                             Body=content, ContentType=content_type)
    except (StorageConfigurationError, StorageOperationError):
        raise
    except Exception as exc:
        raise StorageOperationError("Neon Storageへの保存に失敗しました。") from exc


def download(key: str) -> bytes:
    try:
        response = _client().get_object(Bucket=_settings()["STORAGE_BUCKET"], Key=key)
        return response["Body"].read()
    except (StorageConfigurationError, StorageOperationError):
        raise
    except Exception as exc:
        raise StorageOperationError("Neon Storageからの取得に失敗しました。") from exc


def delete(key: str | None) -> None:
    if not key:
        return
    try:
        _client().delete_object(Bucket=_settings()["STORAGE_BUCKET"], Key=key)
    except (StorageConfigurationError, StorageOperationError):
        raise
    except Exception as exc:
        raise StorageOperationError("Neon Storage上のファイル削除に失敗しました。") from exc


def require_storage():
    try:
        _settings()
        _client()
    except StorageConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except StorageOperationError as exc:
        raise HTTPException(503, "Neon Storageを利用できません。設定を確認してください。") from exc
