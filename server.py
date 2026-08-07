#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import math
import mimetypes
import os
import posixpath
import queue
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, unquote, urlencode, urlparse


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def load_project_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


DEFAULT_DATASET = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes"
PROJECT_ROOT = Path(__file__).resolve().parent
load_project_env_file(PROJECT_ROOT / ".env.dm3")
STATIC_ROOT = PROJECT_ROOT / "web"
QC_ROOT = PROJECT_ROOT / "qc_results"
VIDEO_PROXY_ROOT = PROJECT_ROOT / "video_proxy"
REMOTE_DATASET_CACHE_ROOT = Path(
    os.environ.get("LQCP_REMOTE_DATASET_CACHE_ROOT", str(PROJECT_ROOT / "remote_dataset_cache"))
).expanduser().resolve()
SETTINGS_PATH = QC_ROOT / "settings.json"
USER_SESSIONS_DB_PATH = QC_ROOT / "user_sessions.db"
PROXY_BUILD_HISTORY_PATH = QC_ROOT / "proxy_build_history.jsonl"
ALLOWED_DATASET_ROOT = Path("/mnt").resolve()
RAW_METADATA_TIMEOUT_SECONDS = float(os.environ.get("LQCP_RAW_METADATA_TIMEOUT", "3"))
COLLECTOR_CACHE_WORKERS = max(1, int(os.environ.get("LQCP_COLLECTOR_CACHE_WORKERS", "3")))
COLLECTOR_CACHE_NEGATIVE_TTL_SECONDS = int(os.environ.get("LQCP_COLLECTOR_CACHE_NEGATIVE_TTL", str(24 * 60 * 60)))
DM3_BASE_URL = os.environ.get("LQCP_DM3_BASE_URL", "https://dm3.noematrix.cn").rstrip("/")
DM3_PHONE_NUMBER = (os.environ.get("LQCP_DM3_PHONE_NUMBER") or os.environ.get("LQCP_DM3_PHONE") or "").strip()
DM3_PASSWORD = os.environ.get("LQCP_DM3_PASSWORD", "")
DM3_STATIC_TOKEN = os.environ.get("LQCP_DM3_TOKEN", "").strip()
DM3_TIMEOUT_SECONDS = float(os.environ.get("LQCP_DM3_TIMEOUT", "8"))
DEFAULT_PROXY_BUILD_WORKERS = max(4, min(48, (os.cpu_count() or 8) // 4))
PROXY_BUILD_ON_DATASET_LOAD = env_flag("LQCP_PROXY_BUILD_ON_DATASET_LOAD", True)
PROXY_BUILD_WORKERS = env_int("LQCP_PROXY_BUILD_WORKERS", DEFAULT_PROXY_BUILD_WORKERS, minimum=1, maximum=256)
PROXY_BUILD_CRF = env_int("LQCP_PROXY_CRF", 32, minimum=18, maximum=40)
PROXY_BUILD_PRESET = os.environ.get("LQCP_PROXY_PRESET", "veryfast").strip() or "veryfast"
PROXY_BUILD_ENCODER = os.environ.get("LQCP_PROXY_ENCODER", "auto").strip().lower() or "auto"
PROXY_GPU_SELECT_MODE = os.environ.get("LQCP_PROXY_GPU_SELECT_MODE", "idle").strip().lower() or "idle"
PROXY_GPU_MAX_UTIL = env_int("LQCP_PROXY_GPU_MAX_UTIL", 10, minimum=0, maximum=100)
PROXY_GPU_MAX_MEMORY_RATIO = env_float("LQCP_PROXY_GPU_MAX_MEMORY_RATIO", 0.20, minimum=0.0, maximum=1.0)
PROXY_GPU_MEMORY_MAX_RATIO = env_float("LQCP_PROXY_GPU_MEMORY_MAX_RATIO", 0.80, minimum=0.0, maximum=1.0)
PROXY_GPU_WORKERS_PER_GPU = env_int("LQCP_PROXY_GPU_WORKERS_PER_GPU", 2, minimum=1, maximum=8)
PROXY_GPU_FALLBACK_CPU = env_flag("LQCP_PROXY_GPU_FALLBACK_CPU", True)
PROXY_BUILD_HISTORY_LIMIT = env_int("LQCP_PROXY_BUILD_HISTORY_LIMIT", 200, minimum=1, maximum=5000)
REMOTE_DATASET_SSH_IDENTITY = os.environ.get(
    "LQCP_REMOTE_DATASET_SSH_IDENTITY",
    "/root/.ssh/id_ed25519_lqcp_4110",
).strip()
REMOTE_DATASET_SYNC_TIMEOUT_SECONDS = env_int(
    "LQCP_REMOTE_DATASET_SYNC_TIMEOUT",
    3600,
    minimum=60,
    maximum=24 * 60 * 60,
)

STATUS_VALUES = {"reject", "pending", "accept", "unlabeled"}
STATUS_ALIASES = {"bad": "reject", "review": "pending", "good": "accept"}
RECORDED_STATUS_VALUES = {"reject", "pending", "accept"}
DECISION_STATUS_VALUES = {"reject", "accept"}
EPISODE_RE = re.compile(r"episode_(\d+)\.(mp4|parquet)$")
PRESENCE_TTL_SECONDS = 8.0
USER_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
USER_SESSION_COOKIE = "lqcp_client_id"

DATASET_CACHE: dict[str, dict[str, Any]] = {}
TRAJECTORY_CACHE: dict[tuple[str, int, int, int], dict[str, Any]] = {}
RAW_METADATA_CACHE: dict[str, dict[str, Any]] = {}
COLLECTOR_CACHE_QUEUE: queue.PriorityQueue[tuple[int, int, str, str, int, str]] = queue.PriorityQueue()
COLLECTOR_CACHE_PENDING: dict[tuple[str, int], int] = {}
COLLECTOR_PREFETCH_KEYS: set[tuple[Any, ...]] = set()
COLLECTOR_QUEUE_SEQUENCE = 0
EPISODE_PRESENCE: dict[str, dict[str, dict[str, float]]] = {}
DATASET_CACHE_LOCK = threading.Lock()
RAW_METADATA_LOCK = threading.Lock()
DM3_TOKEN_LOCK = threading.Lock()
COLLECTOR_CACHE_LOCK = threading.Lock()
LABEL_LOCK = threading.Lock()
PRESENCE_LOCK = threading.Lock()
SETTINGS_LOCK = threading.Lock()
REMOTE_DATASET_LOCK = threading.Lock()
USER_SESSION_LOCK = threading.Lock()
PROXY_BUILD_LOCK = threading.Lock()
PROXY_BUILD_HISTORY_LOCK = threading.Lock()
SERVER_CONFIG: dict[str, Any] = {}
DM3_TOKEN = DM3_STATIC_TOKEN
PROXY_BUILD_JOBS: dict[str, "ProxyBuildJob"] = {}
PROXY_BUILD_SEQUENCE = 0
FFMPEG_ENCODERS: set[str] | None = None
NVENC_GPU_CAPABILITY: dict[int, bool] = {}


class QCThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = max(16, int(os.environ.get("LQCP_HTTP_REQUEST_QUEUE_SIZE", "128")))


class QCThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = max(16, int(os.environ.get("LQCP_HTTP_REQUEST_QUEUE_SIZE", "128")))


class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def configured_raw_episode_roots() -> list[Path]:
    raw_roots = os.environ.get("LQCP_RAW_EPISODE_ROOTS", "").strip()
    if raw_roots:
        parts = [part.strip() for part in re.split(r"[,;]", raw_roots) if part.strip()]
    else:
        parts = [
            os.environ.get("LQCP_RAW_NEDF_ROOT", "/mnt/nm_data/data/nedf"),
            os.environ.get("LQCP_RAW_MIDTRAIN_ROOT", "/mnt/nm_data/data/midtrain"),
        ]
    roots = []
    seen = set()
    for part in parts:
        key = str(Path(part))
        if key in seen:
            continue
        seen.add(key)
        roots.append(Path(part))
    return roots


def raw_episode_roots_signature() -> str:
    return json.dumps([str(root) for root in configured_raw_episode_roots()], ensure_ascii=False)


def dm3_enabled() -> bool:
    return bool(DM3_STATIC_TOKEN or (DM3_PHONE_NUMBER and DM3_PASSWORD))


def source_metadata_signature() -> str:
    return json.dumps(
        {
            "dm3": {"enabled": dm3_enabled(), "base_url": DM3_BASE_URL if dm3_enabled() else ""},
            "raw_roots": [str(root) for root in configured_raw_episode_roots()],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def raw_metadata_fallback_enabled() -> bool:
    value = os.environ.get("LQCP_RAW_METADATA_FALLBACK", "")
    if value:
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return not dm3_enabled()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_default(value: Any) -> str:
    return str(value)


def clean_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise AppError(f"Invalid JSONL at {path}:{line_number}: {exc}", 500)
    return rows


def read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=json_default)
        handle.write("\n")
    os.replace(tmp_path, path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp_path, path)


def is_remote_dataset_uri(raw_path: str | None) -> bool:
    return urlparse(str(raw_path or "").strip()).scheme.lower() == "ssh"


def parse_remote_dataset_uri(raw_path: str) -> dict[str, Any]:
    parsed = urlparse(raw_path)
    if parsed.scheme.lower() != "ssh":
        raise AppError("Remote dataset URI must use ssh://", 400)
    if parsed.password:
        raise AppError("Password-bearing SSH URIs are not supported; configure a key instead", 400)
    if not parsed.hostname:
        raise AppError("Remote dataset URI is missing a host", 400)
    remote_path = unquote(parsed.path or "")
    if not remote_path.startswith("/") or ".." in PurePosixPath(remote_path).parts:
        raise AppError("Remote dataset path must be an absolute path without '..'", 400)
    username = parsed.username or "root"
    port = parsed.port or 22
    canonical = f"ssh://{username}@{parsed.hostname}:{port}{remote_path.rstrip('/')}"
    return {
        "source": canonical,
        "username": username,
        "hostname": parsed.hostname,
        "port": port,
        "remote_path": remote_path.rstrip("/"),
        "target": f"{username}@{parsed.hostname}",
    }


def remote_dataset_cache_path(raw_path: str) -> Path:
    remote = parse_remote_dataset_uri(raw_path)
    basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", PurePosixPath(remote["remote_path"]).name).strip("_")
    basename = basename or "remote_dataset"
    digest = hashlib.sha1(remote["source"].encode("utf-8")).hexdigest()[:12]
    return REMOTE_DATASET_CACHE_ROOT / f"{basename}-{digest}"


def remote_dataset_manifest(dataset_path: Path) -> dict[str, Any] | None:
    payload = read_json(dataset_path / ".remote_dataset.json", fallback=None)
    return payload if isinstance(payload, dict) else None


def remote_ssh_args(remote: dict[str, Any]) -> list[str]:
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/root/.ssh/known_hosts_lqcp_remote",
        "-p",
        str(remote["port"]),
    ]
    if REMOTE_DATASET_SSH_IDENTITY:
        args.extend(["-i", REMOTE_DATASET_SSH_IDENTITY])
    args.append(remote["target"])
    return args


def verify_remote_dataset(remote: dict[str, Any]) -> None:
    required = [
        f"{remote['remote_path']}/meta/info.json",
        f"{remote['remote_path']}/meta/episodes.jsonl",
        f"{remote['remote_path']}/meta/tasks.jsonl",
        f"{remote['remote_path']}/data",
        f"{remote['remote_path']}/videos",
    ]
    checks = " && ".join(f"test -e {shlex.quote(path)}" for path in required)
    proc = subprocess.run(
        [*remote_ssh_args(remote), checks],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stdout or "").strip()[-1000:]
        raise AppError(f"Remote dataset is unavailable: {detail or remote['source']}", 502)


def materialize_remote_dataset(raw_path: str, force: bool = False) -> Path:
    remote = parse_remote_dataset_uri(raw_path)
    cache_path = remote_dataset_cache_path(raw_path)
    with REMOTE_DATASET_LOCK:
        manifest = remote_dataset_manifest(cache_path)
        if not force and manifest and manifest.get("source") == remote["source"]:
            require_ready_dataset(cache_path)
            return cache_path

        verify_remote_dataset(remote)
        REMOTE_DATASET_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        staging_path = cache_path.with_name(cache_path.name + ".syncing")
        if staging_path.exists():
            shutil.rmtree(staging_path)
        staging_path.mkdir(parents=True, exist_ok=True)

        ssh_command = " ".join(shlex.quote(part) for part in remote_ssh_args(remote)[:-1])
        rsync_command = [
            "rsync",
            "-aL",
            "--delete",
            "--delete-excluded",
            "--partial",
            "--protect-args",
            "--exclude=/latents/***",
            "--exclude=/meta/latent_sidecars/***",
            "--exclude=/meta/latent_shapes.json",
            "--exclude=/meta/latent_wh.json",
            "-e",
            ssh_command,
            f"{remote['target']}:{remote['remote_path']}/",
            f"{staging_path}/",
        ]
        try:
            proc = subprocess.run(
                rsync_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=REMOTE_DATASET_SYNC_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError(f"Remote dataset sync timed out after {REMOTE_DATASET_SYNC_TIMEOUT_SECONDS}s", 504) from exc
        if proc.returncode != 0:
            detail = (proc.stdout or "").strip()[-2000:]
            raise AppError(f"Remote dataset sync failed: {detail or 'rsync error'}", 502)

        require_ready_dataset(staging_path)
        write_json_atomic(
            staging_path / ".remote_dataset.json",
            {
                "source": remote["source"],
                "synced_at": utc_now(),
                "ssh_identity": REMOTE_DATASET_SSH_IDENTITY,
                "excluded": ["latents", "meta/latent_sidecars", "meta/latent_shapes.json", "meta/latent_wh.json"],
            },
        )
        if cache_path.exists():
            shutil.rmtree(cache_path)
        os.replace(staging_path, cache_path)
        return cache_path


def load_server_settings() -> dict[str, Any]:
    payload = read_json(SETTINGS_PATH, fallback={})
    return payload if isinstance(payload, dict) else {}


def current_dataset_raw_path() -> str:
    settings = load_server_settings()
    dataset_path = str(settings.get("dataset_path") or "").strip()
    if dataset_path:
        return dataset_path
    dataset_source = str(settings.get("dataset_source") or "").strip()
    if dataset_source:
        return dataset_source
    return str(SERVER_CONFIG.get("default_dataset") or DEFAULT_DATASET)


def configured_dataset_sources(settings: dict[str, Any] | None = None) -> list[str]:
    settings = settings if isinstance(settings, dict) else load_server_settings()
    raw_paths = settings.get("dataset_paths")
    if isinstance(raw_paths, list):
        sources = [str(value or "").strip() for value in raw_paths]
        sources = [value for value in sources if value]
        if sources:
            return sources
    source = str(settings.get("dataset_source") or settings.get("dataset_path") or "").strip()
    return [source] if source else []


def configured_dataset_path(raw_path: Any) -> Path | None:
    value = str(raw_path or "").strip()
    if not value:
        return None
    try:
        if is_remote_dataset_uri(value):
            return remote_dataset_cache_path(value)
        return Path(value).expanduser().resolve()
    except (AppError, OSError, ValueError):
        return None


def dataset_source_for_path(dataset_path: Path, settings: dict[str, Any] | None = None) -> str:
    resolved_path = dataset_path.expanduser().resolve()
    manifest = remote_dataset_manifest(resolved_path)
    manifest_source = str((manifest or {}).get("source") or "").strip()
    if manifest_source and configured_dataset_path(manifest_source) == resolved_path:
        return manifest_source

    settings = settings if isinstance(settings, dict) else load_server_settings()
    settings_source = str(settings.get("dataset_source") or "").strip()
    source_path = configured_dataset_path(settings_source)
    if source_path == resolved_path:
        return settings_source
    return str(resolved_path)


def safe_dataset_path(raw_path: str | None) -> Path:
    raw_path = raw_path or current_dataset_raw_path()
    if is_remote_dataset_uri(raw_path):
        dataset_path = remote_dataset_cache_path(str(raw_path))
    else:
        dataset_path = Path(raw_path).expanduser().resolve()
    allowed = str(ALLOWED_DATASET_ROOT)
    if str(dataset_path) != allowed and not str(dataset_path).startswith(allowed + os.sep):
        raise AppError("Dataset path must be under /mnt", 403)
    if not dataset_path.is_dir():
        raise AppError(f"Dataset path does not exist: {dataset_path}", 404)
    episodes_path = dataset_path / "meta" / "episodes.jsonl"
    if not episodes_path.exists():
        raise AppError(f"Dataset is missing meta/episodes.jsonl: {dataset_path}", 400)
    return dataset_path


def canonical_dataset_source(raw_path: str) -> str:
    value = str(raw_path or "").strip()
    if is_remote_dataset_uri(value):
        return parse_remote_dataset_uri(value)["source"]
    return str(Path(value).expanduser().resolve())


def dataset_source_name(source: str, fallback_path: Path | None = None) -> str:
    if is_remote_dataset_uri(source):
        return PurePosixPath(parse_remote_dataset_uri(source)["remote_path"]).name
    return Path(source).name or (fallback_path.name if fallback_path is not None else "dataset")


def dataset_catalog(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = settings if isinstance(settings, dict) else load_server_settings()
    active_id = str(settings.get("active_dataset_id") or "").strip()
    catalog: list[dict[str, Any]] = []
    for raw_source in configured_dataset_sources(settings):
        try:
            source = canonical_dataset_source(raw_source)
            path = configured_dataset_path(source)
            if path is None:
                continue
            info = read_json(path / "meta" / "info.json", fallback={}) or {}
            ready = all(
                item.exists()
                for item in (
                    path / "meta" / "info.json",
                    path / "meta" / "episodes.jsonl",
                    path / "meta" / "tasks.jsonl",
                    path / "data",
                    path / "videos",
                )
            )
            item = {
                "dataset_id": dataset_id(path),
                "dataset_path": str(path),
                "dataset_source": dataset_source_for_path(path, settings),
                "remote_dataset": remote_dataset_manifest(path),
                "ready": ready,
                "total_episodes": info.get("total_episodes"),
                "total_frames": info.get("total_frames"),
                "fps": info.get("fps"),
            }
            item["active"] = item["dataset_id"] == active_id
            catalog.append(item)
        except (AppError, OSError, ValueError, TypeError):
            continue
    return catalog


def current_dataset_payload(dataset_path: Path, dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_server_settings()
    info = (dataset or {}).get("info") or {}
    episodes = (dataset or {}).get("episodes") or []
    return {
        "dataset_path": str(dataset_path),
        "dataset_source": dataset_source_for_path(dataset_path, settings),
        "remote_dataset": remote_dataset_manifest(dataset_path),
        "dataset_id": dataset_id(dataset_path),
        "active_dataset_id": settings.get("active_dataset_id") or dataset_id(dataset_path),
        "dataset_paths": configured_dataset_sources(settings),
        "datasets": dataset_catalog(settings),
        "default_dataset": SERVER_CONFIG.get("default_dataset") or DEFAULT_DATASET,
        "settings_path": str(SETTINGS_PATH),
        "updated_at": settings.get("updated_at"),
        "updated_by": settings.get("updated_by"),
        "total_episodes": info.get("total_episodes", len(episodes) if episodes else None),
        "total_frames": info.get("total_frames"),
        "fps": info.get("fps"),
    }


def require_ready_dataset(dataset_path: Path) -> None:
    required = [
        dataset_path / "meta" / "info.json",
        dataset_path / "meta" / "episodes.jsonl",
        dataset_path / "meta" / "tasks.jsonl",
        dataset_path / "data",
        dataset_path / "videos",
    ]
    missing = [str(path.relative_to(dataset_path)) for path in required if not path.exists()]
    if missing:
        raise AppError(f"Dataset is not ready; missing: {', '.join(missing)}", 400)


def save_configured_datasets(
    raw_paths: Any,
    user: str,
    refresh_remote: bool = False,
    active_dataset_source: str | None = None,
) -> dict[str, Any]:
    existing_settings = load_server_settings()
    if isinstance(raw_paths, list):
        sources = [str(value or "").strip() for value in raw_paths]
        sources = [value for value in sources if value]
    else:
        value = str(raw_paths or "").strip()
        sources = [value] if value else []
    if not sources:
        sources = configured_dataset_sources(existing_settings)
    if not sources:
        raise AppError("dataset_paths is required", 400)

    loaded: list[dict[str, Any]] = []
    for raw_path in sources:
        source = canonical_dataset_source(raw_path)
        remote_dataset = is_remote_dataset_uri(source)
        dataset_path = (
            materialize_remote_dataset(source, force=refresh_remote)
            if remote_dataset
            else safe_dataset_path(source)
        )
        require_ready_dataset(dataset_path)
        dataset = load_dataset(dataset_path, refresh=True)
        loaded.append({"source": source, "path": dataset_path, "dataset": dataset})

    active_path: Path | None = None
    active_source = str(active_dataset_source or "").strip()
    if active_source:
        active_path = configured_dataset_path(active_source)
    if active_path is None:
        previous_source = str(existing_settings.get("dataset_source") or "").strip()
        previous_path = configured_dataset_path(previous_source) if previous_source else None
        active_path = previous_path
    active = next((item for item in loaded if item["path"] == active_path), None)
    if active is None:
        active = loaded[0]

    settings_payload = {
        "dataset_paths": [item["source"] for item in loaded],
        "dataset_path": str(active["path"]),
        "dataset_source": active["source"],
        "active_dataset_id": dataset_id(active["path"]),
        "default_dataset": existing_settings.get("default_dataset")
        or SERVER_CONFIG.get("default_dataset")
        or DEFAULT_DATASET,
        "updated_at": utc_now(),
        "updated_by": user,
    }
    with SETTINGS_LOCK:
        write_json_atomic(SETTINGS_PATH, settings_payload)
    return current_dataset_payload(active["path"], active["dataset"])


def save_current_dataset(raw_path: str | None, user: str, refresh_remote: bool = False) -> dict[str, Any]:
    return save_configured_datasets(raw_path, user, refresh_remote=refresh_remote)


def dataset_id(dataset_path: Path) -> str:
    digest = hashlib.sha1(str(dataset_path).encode("utf-8")).hexdigest()[:12]
    basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset_path.name).strip("_") or "dataset"
    return f"{basename}-{digest}"


def normalize_user(raw_user: str | None) -> str:
    raw_user = (raw_user or "").strip()
    if not raw_user:
        return "default"
    user = re.sub(r"[^\w.@-]+", "_", raw_user, flags=re.UNICODE).strip("_")
    return (user or "default")[:64]


def connect_user_session_db() -> sqlite3.Connection:
    QC_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USER_SESSIONS_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            client_id TEXT PRIMARY KEY,
            user TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_used_at REAL NOT NULL
        )
        """
    )
    return conn


def cleanup_user_sessions(conn: sqlite3.Connection) -> None:
    cutoff = time.time() - USER_SESSION_TTL_SECONDS
    conn.execute("DELETE FROM user_sessions WHERE last_used_at < ?", (cutoff,))


def generate_client_id() -> str:
    return secrets.token_urlsafe(24)


def is_valid_client_id(client_id: str | None) -> bool:
    return bool(client_id and re.fullmatch(r"[A-Za-z0-9_-]{16,96}", client_id))


def session_row_payload(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {"user": None, "last_used_at": None}
    return {
        "user": row["user"],
        "last_used_at": datetime.fromtimestamp(float(row["last_used_at"]), timezone.utc).isoformat(timespec="seconds"),
    }


def read_user_session(client_id: str) -> dict[str, Any]:
    with USER_SESSION_LOCK, connect_user_session_db() as conn:
        cleanup_user_sessions(conn)
        row = conn.execute(
            "SELECT user, last_used_at FROM user_sessions WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        return session_row_payload(row)


def save_user_session(client_id: str, user: str) -> dict[str, Any]:
    now = time.time()
    with USER_SESSION_LOCK, connect_user_session_db() as conn:
        cleanup_user_sessions(conn)
        conn.execute(
            """
            INSERT INTO user_sessions(client_id, user, created_at, last_used_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                user = excluded.user,
                last_used_at = excluded.last_used_at
            """,
            (client_id, user, now, now),
        )
        row = conn.execute(
            "SELECT user, last_used_at FROM user_sessions WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        return session_row_payload(row)


def labels_dir(dataset_path: Path) -> Path:
    return QC_ROOT / dataset_id(dataset_path)


def labels_path(dataset_path: Path) -> Path:
    return labels_dir(dataset_path) / "labels.json"


def labels_jsonl_path(dataset_path: Path) -> Path:
    return labels_dir(dataset_path) / "labels.jsonl"


def labels_db_path(dataset_path: Path) -> Path:
    return labels_dir(dataset_path) / "labels.db"


def empty_label_store(dataset_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "dataset_path": str(dataset_path),
        "dataset_id": dataset_id(dataset_path),
        "updated_at": utc_now(),
        "labels": {},
        "labels_by_user": {},
    }


def normalize_label_entry(label: Any, fallback_user: str) -> dict[str, Any] | None:
    if not isinstance(label, dict):
        return None
    normalized = dict(label)
    old_status = normalized.get("status")
    if old_status in STATUS_ALIASES:
        normalized["status"] = STATUS_ALIASES[old_status]
    normalized["status"] = review_status(normalized.get("status"))
    user = normalize_user(str(normalized.get("user") or normalized.get("annotator") or fallback_user))
    normalized["user"] = user
    normalized["annotator"] = normalize_user(str(normalized.get("annotator") or user))
    return normalized


def normalize_label_store(dataset_path: Path, payload: Any) -> dict[str, Any]:
    store = empty_label_store(dataset_path)
    if not isinstance(payload, dict):
        return store

    global_labels: dict[str, dict[str, Any]] = {}
    labels_by_user: dict[str, dict[str, Any]] = {}

    raw_global = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    for key, label in raw_global.items():
        normalized = normalize_label_entry(label, str(label.get("user") or "default") if isinstance(label, dict) else "default")
        if normalized:
            global_labels[str(key)] = normalized

    if isinstance(payload.get("labels_by_user"), dict):
        raw_by_user = payload.get("labels_by_user") or {}
    elif global_labels:
        raw_by_user = {}
    else:
        raw_by_user = {"default": payload}

    for raw_user, raw_labels in raw_by_user.items():
        user = normalize_user(str(raw_user))
        if not isinstance(raw_labels, dict):
            continue
        user_labels = {}
        for key, label in raw_labels.items():
            normalized = normalize_label_entry(label, user)
            if not normalized:
                continue
            key = str(key)
            user_labels[key] = normalized
            existing = global_labels.get(key)
            if not existing or str(normalized.get("updated_at", "")) >= str(existing.get("updated_at", "")):
                global_labels[key] = normalized
        labels_by_user[user] = user_labels

    for key, label in global_labels.items():
        user = normalize_user(str(label.get("user") or label.get("annotator") or "default"))
        labels_by_user.setdefault(user, {}).setdefault(key, label)

    store["labels"] = global_labels
    store["labels_by_user"] = labels_by_user
    store["updated_at"] = payload.get("updated_at") or store["updated_at"]
    return store


def connect_label_db(dataset_path: Path) -> sqlite3.Connection:
    labels_dir(dataset_path).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(labels_db_path(dataset_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_label_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS labels (
            dataset_id TEXT NOT NULL,
            episode_index INTEGER NOT NULL,
            dataset_path TEXT NOT NULL,
            user TEXT NOT NULL,
            annotator TEXT NOT NULL,
            episode_name TEXT NOT NULL,
            episode_uuid TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            issues_json TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (dataset_id, episode_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS label_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT NOT NULL,
            episode_index INTEGER NOT NULL,
            user TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            label_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_label_events_episode ON label_events(dataset_id, episode_index, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collector_cache (
            dataset_id TEXT NOT NULL,
            episode_index INTEGER NOT NULL,
            dataset_path TEXT NOT NULL,
            episode_name TEXT NOT NULL DEFAULT '',
            episode_uuid TEXT NOT NULL DEFAULT '',
            collector TEXT NOT NULL DEFAULT '',
            seat TEXT NOT NULL DEFAULT '',
            seat_number TEXT NOT NULL DEFAULT '',
            device TEXT NOT NULL DEFAULT '',
            device_id TEXT NOT NULL DEFAULT '',
            device_identifier TEXT NOT NULL DEFAULT '',
            task TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '',
            raw_path TEXT NOT NULL DEFAULT '',
            metadata_path TEXT NOT NULL DEFAULT '',
            raw_roots TEXT NOT NULL DEFAULT '',
            found INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'missing',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (dataset_id, episode_index)
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(collector_cache)")}
    for column_name in (
        "seat",
        "seat_number",
        "device",
        "device_id",
        "device_identifier",
        "task",
        "metadata_json",
        "raw_roots",
    ):
        if column_name not in columns:
            conn.execute(f"ALTER TABLE collector_cache ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collector_cache_uuid ON collector_cache(dataset_id, episode_uuid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collector_cache_collector ON collector_cache(dataset_id, collector)")
    conn.commit()


def db_row_to_label(row: sqlite3.Row) -> dict[str, Any]:
    try:
        issues = json.loads(row["issues_json"] or "[]")
    except json.JSONDecodeError:
        issues = []
    if not isinstance(issues, list):
        issues = []
    return {
        "dataset_id": row["dataset_id"],
        "dataset_path": row["dataset_path"],
        "user": row["user"],
        "annotator": row["annotator"],
        "episode_index": int(row["episode_index"]),
        "episode_name": row["episode_name"],
        "episode_uuid": row["episode_uuid"],
        "status": review_status(row["status"]),
        "issues": issues,
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


def store_from_label_db(dataset_path: Path, conn: sqlite3.Connection) -> dict[str, Any]:
    store = empty_label_store(dataset_path)
    rows = conn.execute(
        """
        SELECT dataset_id, episode_index, dataset_path, user, annotator, episode_name,
               episode_uuid, status, issues_json, note, updated_at
        FROM labels
        WHERE dataset_id = ?
        ORDER BY episode_index
        """,
        (dataset_id(dataset_path),),
    ).fetchall()
    labels: dict[str, dict[str, Any]] = {}
    labels_by_user: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = db_row_to_label(row)
        key = str(label["episode_index"])
        labels[key] = label
        labels_by_user.setdefault(label["user"], {})[key] = label
    store["labels"] = labels
    store["labels_by_user"] = labels_by_user
    if rows:
        store["updated_at"] = max(str(row["updated_at"]) for row in rows)
    return store


def import_json_labels_if_needed(dataset_path: Path, conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM labels WHERE dataset_id = ?", (dataset_id(dataset_path),)).fetchone()[0]
    if count:
        return
    payload = read_json(labels_path(dataset_path), fallback=None)
    store = normalize_label_store(dataset_path, payload)
    labels = store.get("labels") or {}
    if not labels:
        return
    now = utc_now()
    for key, label in labels.items():
        normalized = normalize_label_entry(label, str(label.get("user") or "default"))
        if not normalized:
            continue
        episode_index = int(normalized.get("episode_index") or key)
        normalized["episode_index"] = episode_index
        normalized["dataset_id"] = dataset_id(dataset_path)
        normalized["dataset_path"] = str(dataset_path)
        normalized.setdefault("episode_name", f"episode_{episode_index:06d}")
        normalized.setdefault("episode_uuid", "")
        normalized.setdefault("issues", [])
        normalized.setdefault("note", "")
        normalized.setdefault("updated_at", now)
        conn.execute(
            """
            INSERT OR REPLACE INTO labels (
                dataset_id, episode_index, dataset_path, user, annotator, episode_name,
                episode_uuid, status, issues_json, note, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["dataset_id"],
                episode_index,
                normalized["dataset_path"],
                normalized["user"],
                normalized["annotator"],
                normalized["episode_name"],
                normalized["episode_uuid"],
                review_status(normalized["status"]),
                json.dumps(normalized.get("issues") or [], ensure_ascii=False),
                str(normalized.get("note") or ""),
                normalized["updated_at"],
            ),
        )
        conn.execute(
            """
            INSERT INTO label_events (
                dataset_id, episode_index, user, old_status, new_status, label_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["dataset_id"],
                episode_index,
                normalized["user"],
                None,
                review_status(normalized["status"]),
                json.dumps(normalized, ensure_ascii=False, default=json_default),
                now,
            ),
        )
    conn.commit()


def export_label_store(dataset_path: Path, store: dict[str, Any]) -> None:
    store["schema_version"] = 4
    store["dataset_path"] = str(dataset_path)
    store["dataset_id"] = dataset_id(dataset_path)
    store["updated_at"] = utc_now()
    store.setdefault("labels", {})
    store.setdefault("labels_by_user", {})
    write_json_atomic(labels_path(dataset_path), store)

    lines = []
    for row in label_rows_from_store(dataset_path, {}, store):
        lines.append(json.dumps(row, ensure_ascii=False, default=json_default))
    write_text_atomic(labels_jsonl_path(dataset_path), "\n".join(lines) + ("\n" if lines else ""))


def load_label_store(dataset_path: Path) -> dict[str, Any]:
    with connect_label_db(dataset_path) as conn:
        init_label_db(conn)
        import_json_labels_if_needed(dataset_path, conn)
        return store_from_label_db(dataset_path, conn)


def save_label_store(dataset_path: Path, store: dict[str, Any]) -> None:
    export_label_store(dataset_path, store)


def write_label_db(
    dataset_path: Path,
    dataset: dict[str, Any],
    user: str,
    episode_index: int,
    status: str,
    issues: list[str],
    note: str,
    force: bool = False,
) -> dict[str, Any]:
    now = utc_now()
    with connect_label_db(dataset_path) as conn:
        init_label_db(conn)
        import_json_labels_if_needed(dataset_path, conn)
        conn.execute("BEGIN IMMEDIATE")
        existing_row = conn.execute(
            """
            SELECT dataset_id, episode_index, dataset_path, user, annotator, episode_name,
                   episode_uuid, status, issues_json, note, updated_at
            FROM labels
            WHERE dataset_id = ? AND episode_index = ?
            """,
            (dataset_id(dataset_path), episode_index),
        ).fetchone()
        existing = db_row_to_label(existing_row) if existing_row else None
        old_status = existing.get("status") if existing else None

        if status == "unlabeled":
            conn.execute(
                "DELETE FROM labels WHERE dataset_id = ? AND episode_index = ?",
                (dataset_id(dataset_path), episode_index),
            )
            event_label = existing or {
                "dataset_id": dataset_id(dataset_path),
                "dataset_path": str(dataset_path),
                "user": user,
                "annotator": user,
                "episode_index": episode_index,
                "episode_name": f"episode_{episode_index:06d}",
                "episode_uuid": "",
                "status": "unlabeled",
                "issues": [],
                "note": "",
                "updated_at": now,
            }
            event_label = dict(event_label, status="unlabeled", updated_at=now, user=user, annotator=user)
            conn.execute(
                """
                INSERT INTO label_events (
                    dataset_id, episode_index, user, old_status, new_status, label_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id(dataset_path),
                    episode_index,
                    user,
                    old_status,
                    "unlabeled",
                    json.dumps(event_label, ensure_ascii=False, default=json_default),
                    now,
                ),
            )
        else:
            if existing and existing.get("user") != user and review_status(existing.get("status")) != review_status(status) and not force:
                conn.rollback()
                raise AppError(
                    f"Episode {episode_index} already labeled {existing.get('status')} by {existing.get('user')}",
                    409,
                )
            if existing and existing.get("user") != user and review_status(existing.get("status")) == review_status(status) and not force:
                label = existing
            else:
                episode = dataset["episode_by_index"][episode_index]
                label = {
                    "dataset_id": dataset_id(dataset_path),
                    "dataset_path": str(dataset_path),
                    "user": user,
                    "annotator": user,
                    "episode_index": episode_index,
                    "episode_name": f"episode_{episode_index:06d}",
                    "episode_uuid": episode.get("episode_uuid", ""),
                    "status": status,
                    "issues": issues,
                    "note": note,
                    "updated_at": now,
                }
                conn.execute(
                    """
                    INSERT INTO labels (
                        dataset_id, episode_index, dataset_path, user, annotator, episode_name,
                        episode_uuid, status, issues_json, note, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dataset_id, episode_index) DO UPDATE SET
                        dataset_path = excluded.dataset_path,
                        user = excluded.user,
                        annotator = excluded.annotator,
                        episode_name = excluded.episode_name,
                        episode_uuid = excluded.episode_uuid,
                        status = excluded.status,
                        issues_json = excluded.issues_json,
                        note = excluded.note,
                        updated_at = excluded.updated_at
                    """,
                    (
                        label["dataset_id"],
                        episode_index,
                        label["dataset_path"],
                        label["user"],
                        label["annotator"],
                        label["episode_name"],
                        label["episode_uuid"],
                        review_status(label["status"]),
                        json.dumps(label.get("issues") or [], ensure_ascii=False),
                        label["note"],
                        label["updated_at"],
                    ),
                )
            conn.execute(
                """
                INSERT INTO label_events (
                    dataset_id, episode_index, user, old_status, new_status, label_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id(dataset_path),
                    episode_index,
                    user,
                    old_status,
                    review_status(label["status"]),
                    json.dumps(label, ensure_ascii=False, default=json_default),
                    now,
                ),
            )

        conn.commit()
        store = store_from_label_db(dataset_path, conn)

    export_label_store(dataset_path, store)
    return store


def labels_for_user(store: dict[str, Any], user: str) -> dict[str, Any]:
    labels_by_user = store.setdefault("labels_by_user", {})
    return labels_by_user.setdefault(user, {})


def global_labels(store: dict[str, Any]) -> dict[str, Any]:
    return store.setdefault("labels", {})


def label_for_episode(store: dict[str, Any], user: str, episode_index: int) -> dict[str, Any]:
    labels = global_labels(store)
    return labels.get(str(episode_index), {"status": "pending", "issues": [], "note": "", "user": user, "annotator": user})


def review_status(status: Any) -> str:
    if status in DECISION_STATUS_VALUES:
        return str(status)
    return "pending"


def per_episode_label_summary(store: dict[str, Any], episode_index: int) -> dict[str, Any]:
    statuses = {"reject": 0, "pending": 0, "accept": 0}
    users = []
    effective_label = None
    label = (store.get("labels") or {}).get(str(episode_index))
    if label:
        status = review_status(label.get("status"))
        if status in RECORDED_STATUS_VALUES:
            statuses[status] += 1
            user = str(label.get("user") or label.get("annotator") or "default")
            users.append(user)
            if status in DECISION_STATUS_VALUES:
                effective_label = {
                    "episode_index": episode_index,
                    "status": status,
                    "user": user,
                    "annotator": str(label.get("annotator") or user),
                    "updated_at": str(label.get("updated_at") or ""),
                }
    return {
        "label_count": sum(statuses.values()),
        "statuses": statuses,
        "users": users,
        "effective_label": effective_label,
    }


def prune_presence_locked(dataset_key: str, now: float) -> None:
    dataset_presence = EPISODE_PRESENCE.get(dataset_key)
    if not dataset_presence:
        return
    for episode_key, users in list(dataset_presence.items()):
        for user, last_seen in list(users.items()):
            if now - last_seen > PRESENCE_TTL_SECONDS:
                users.pop(user, None)
        if not users:
            dataset_presence.pop(episode_key, None)
    if not dataset_presence:
        EPISODE_PRESENCE.pop(dataset_key, None)


def heartbeat_episode(dataset_path: Path, user: str, episode_index: int) -> None:
    dataset_key = dataset_id(dataset_path)
    now = time.monotonic()
    with PRESENCE_LOCK:
        prune_presence_locked(dataset_key, now)
        EPISODE_PRESENCE.setdefault(dataset_key, {}).setdefault(str(episode_index), {})[user] = now


def release_episode_presence(dataset_path: Path, user: str, episode_index: int) -> None:
    dataset_key = dataset_id(dataset_path)
    now = time.monotonic()
    with PRESENCE_LOCK:
        prune_presence_locked(dataset_key, now)
        users = EPISODE_PRESENCE.get(dataset_key, {}).get(str(episode_index), {})
        users.pop(user, None)


def presence_snapshot(dataset_path: Path, current_user: str | None = None) -> dict[int, list[str]]:
    dataset_key = dataset_id(dataset_path)
    now = time.monotonic()
    snapshot: dict[int, list[str]] = {}
    with PRESENCE_LOCK:
        prune_presence_locked(dataset_key, now)
        for episode_key, users in (EPISODE_PRESENCE.get(dataset_key) or {}).items():
            active = sorted(user for user in users if user != current_user)
            if not active:
                continue
            try:
                snapshot[int(episode_key)] = active
            except ValueError:
                continue
    return snapshot


def mtime_or_zero(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


def camera_name_from_rel(rel_path: str) -> str:
    parent = PurePosixPath(rel_path).parent.name
    return parent.replace("observation.images.", "")


def scan_videos(dataset_path: Path) -> dict[int, list[dict[str, Any]]]:
    videos_root = dataset_path / "videos"
    videos_by_episode: dict[int, list[dict[str, Any]]] = {}
    if not videos_root.exists():
        return videos_by_episode
    for video_path in sorted(videos_root.glob("chunk-*/*/episode_*.mp4")):
        match = EPISODE_RE.match(video_path.name)
        if not match:
            continue
        episode_index = int(match.group(1))
        rel_path = video_path.relative_to(dataset_path).as_posix()
        try:
            size = video_path.stat().st_size
        except OSError:
            size = 0
        videos_by_episode.setdefault(episode_index, []).append(
            {
                "camera": camera_name_from_rel(rel_path),
                "key": PurePosixPath(rel_path).parent.name,
                "rel_path": rel_path,
                "size": size,
            }
        )
    camera_order = {"image": 0, "wrist_image_1": 1, "wrist_image_2": 2}
    for videos in videos_by_episode.values():
        videos.sort(key=lambda item: (camera_order.get(item["camera"], 99), item["camera"]))
    return videos_by_episode


def scan_data_files(dataset_path: Path) -> dict[int, str]:
    data_root = dataset_path / "data"
    data_files: dict[int, str] = {}
    if not data_root.exists():
        return data_files
    for parquet_path in sorted(data_root.glob("chunk-*/episode_*.parquet")):
        match = EPISODE_RE.match(parquet_path.name)
        if not match:
            continue
        data_files[int(match.group(1))] = parquet_path.relative_to(dataset_path).as_posix()
    return data_files


def proxy_rel_path(rel_path: str) -> Path:
    return Path(*PurePosixPath(rel_path).parts)


def proxy_output_path_for_rel(dataset_path: Path, rel_path: str) -> Path:
    proxy_root = (VIDEO_PROXY_ROOT / dataset_id(dataset_path)).resolve()
    file_path = (proxy_root / proxy_rel_path(rel_path)).resolve()
    if str(file_path) != str(proxy_root) and not str(file_path).startswith(str(proxy_root) + os.sep):
        raise AppError("Invalid proxy media path", 403)
    return file_path


def proxy_needs_rebuild(src: Path, dst: Path) -> bool:
    try:
        if not dst.is_file() or dst.stat().st_size <= 0:
            return True
        return dst.stat().st_mtime_ns < src.stat().st_mtime_ns
    except OSError:
        return True


def ffmpeg_encoder_available(encoder: str) -> bool:
    global FFMPEG_ENCODERS
    if FFMPEG_ENCODERS is None:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
            FFMPEG_ENCODERS = set(re.findall(r"\s([A-Za-z0-9_]+)\s+", proc.stdout or ""))
        except Exception:
            FFMPEG_ENCODERS = set()
    return encoder in FFMPEG_ENCODERS


def gpu_statuses() -> list[dict[str, Any]]:
    if not ffmpeg_encoder_available("h264_nvenc"):
        return []
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    statuses = []
    for line in (proc.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            index = int(parts[0])
            util = int(float(parts[1]))
            mem_used = float(parts[2])
            mem_total = max(1.0, float(parts[3]))
        except ValueError:
            continue
        memory_ratio = mem_used / mem_total
        statuses.append(
            {
                "index": index,
                "util": util,
                "memory_used": mem_used,
                "memory_total": mem_total,
                "memory_ratio": memory_ratio,
            }
        )
    return statuses


def candidate_gpu_indices() -> list[int]:
    mode = PROXY_GPU_SELECT_MODE
    statuses = gpu_statuses()
    if mode in {"memory", "memory_available", "mem"}:
        return [
            int(status["index"])
            for status in statuses
            if float(status["memory_ratio"]) <= PROXY_GPU_MEMORY_MAX_RATIO
        ]
    return [
        int(status["index"])
        for status in statuses
        if int(status["util"]) <= PROXY_GPU_MAX_UTIL and float(status["memory_ratio"]) <= PROXY_GPU_MAX_MEMORY_RATIO
    ]


def nvenc_probe_gpu(gpu_index: int) -> bool:
    cached = NVENC_GPU_CAPABILITY.get(gpu_index)
    if cached is not None:
        return cached
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=16x16:r=1:d=0.1",
        "-frames:v",
        "1",
        "-c:v",
        "h264_nvenc",
        "-gpu",
        str(gpu_index),
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=8, check=False)
        ok = proc.returncode == 0
    except Exception:
        ok = False
    NVENC_GPU_CAPABILITY[gpu_index] = ok
    return ok


def nvenc_capable_gpu_indices(indices: list[int]) -> list[int]:
    if not indices:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(indices))) as pool:
        results = list(pool.map(nvenc_probe_gpu, indices))
    return [index for index, capable in zip(indices, results) if capable]


def choose_proxy_encoder() -> tuple[str, list[int]]:
    requested = PROXY_BUILD_ENCODER
    if requested in {"h264_nvenc", "nvenc", "gpu", "auto"}:
        capable = nvenc_capable_gpu_indices(candidate_gpu_indices())
        if capable and requested != "libx264":
            return "h264_nvenc", capable
        if requested in {"h264_nvenc", "nvenc", "gpu"}:
            return "libx264", []
    return "libx264", []


def proxy_worker_count(encoder: str, gpu_indices: list[int]) -> int:
    if encoder == "h264_nvenc" and gpu_indices:
        return max(1, min(PROXY_BUILD_WORKERS, len(gpu_indices) * PROXY_GPU_WORKERS_PER_GPU))
    return PROXY_BUILD_WORKERS


def transcode_proxy_video(src: Path, dst: Path, encoder: str, gpu_index: int | None = None) -> tuple[str, str]:
    if not proxy_needs_rebuild(src, dst):
        return "skipped", ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp.mp4")
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass
    base_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-an",
    ]
    if encoder == "h264_nvenc":
        cmd = [
            *base_cmd,
            "-c:v",
            "h264_nvenc",
            "-preset",
            "fast",
            "-rc",
            "vbr",
            "-cq",
            str(PROXY_BUILD_CRF),
            "-b:v",
            "0",
        ]
        if gpu_index is not None:
            cmd.extend(["-gpu", str(gpu_index)])
        cmd.extend(["-pix_fmt", "yuv420p", "-profile:v", "main", "-level", "3.1", "-movflags", "+faststart", str(tmp)])
    else:
        cmd = [
            *base_cmd,
            "-c:v",
            "libx264",
            "-preset",
            PROXY_BUILD_PRESET,
            "-crf",
            str(PROXY_BUILD_CRF),
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "main",
            "-level",
            "3.1",
            "-movflags",
            "+faststart",
            "-threads",
            "1",
            str(tmp),
        ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    if proc.returncode != 0:
        try:
            tmp.unlink()
        except OSError:
            pass
        return "failed", (proc.stderr or "")[-500:]
    os.replace(tmp, dst)
    return "built", ""


def parse_utc_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def proxy_build_duration_seconds(started_at: str, ended_at: str) -> float | None:
    start = parse_utc_timestamp(started_at)
    end = parse_utc_timestamp(ended_at)
    if not start or not end:
        return None
    return max(0.0, round((end - start).total_seconds(), 3))


def proxy_build_rate(done: int, duration_seconds: float | None) -> float | None:
    if not duration_seconds or duration_seconds <= 0:
        return None
    return round(done / duration_seconds, 3)


def append_proxy_build_history(record: dict[str, Any]) -> None:
    try:
        with PROXY_BUILD_HISTORY_LOCK:
            PROXY_BUILD_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with PROXY_BUILD_HISTORY_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=json_default) + "\n")
    except Exception as exc:
        print(f"Warning: failed to write proxy build history: {exc}", file=sys.stderr, flush=True)


def read_proxy_build_history(dataset_path: Path | None = None, limit: int = PROXY_BUILD_HISTORY_LIMIT) -> list[dict[str, Any]]:
    if not PROXY_BUILD_HISTORY_PATH.exists():
        return []
    dataset_key = dataset_id(dataset_path) if dataset_path is not None else ""
    rows: list[dict[str, Any]] = []
    with PROXY_BUILD_HISTORY_LOCK:
        with PROXY_BUILD_HISTORY_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if dataset_key and row.get("dataset_id") != dataset_key:
                    continue
                rows.append(row)
    return rows[-limit:]


class ProxyBuildJob:
    def __init__(self, dataset_path: Path, dataset: dict[str, Any], reason: str = "load") -> None:
        self.dataset_path = dataset_path
        self.dataset_id = dataset_id(dataset_path)
        self.dataset_fingerprint = tuple(dataset.get("fingerprint") or ())
        self.output_root = str((VIDEO_PROXY_ROOT / self.dataset_id).resolve())
        self.created_at = utc_now()
        self.updated_at = self.created_at
        self.finished_at = ""
        self.reason = reason
        self.status = "queued"
        self.encoder = "pending"
        self.gpu_indices: list[int] = []
        self.worker_count = 0
        self.total = 0
        self.built = 0
        self.skipped = 0
        self.failed = 0
        self.cancel_requested = False
        self.videos_by_rel: dict[str, dict[str, Any]] = {}
        self.queued_priorities: dict[str, int] = {}
        self.in_progress: set[str] = set()
        self.done: set[str] = set()
        self.failed_items: list[dict[str, str]] = []
        self.priority_episodes: list[int] = []
        self.queue: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
        self.lock = threading.Lock()
        for videos in (dataset.get("videos_by_episode") or {}).values():
            for video in videos:
                rel_path = str(video.get("rel_path") or "")
                if not rel_path:
                    continue
                self.videos_by_rel[rel_path] = dict(video)
        self.total = len(self.videos_by_rel)

    def enqueue(self, rel_path: str, priority: int) -> bool:
        global PROXY_BUILD_SEQUENCE
        if rel_path not in self.videos_by_rel:
            return False
        with self.lock:
            if rel_path in self.done or rel_path in self.in_progress:
                return False
            existing = self.queued_priorities.get(rel_path)
            if existing is not None and existing <= priority:
                return False
            PROXY_BUILD_SEQUENCE += 1
            self.queued_priorities[rel_path] = priority
            self.queue.put((priority, PROXY_BUILD_SEQUENCE, rel_path))
            self.updated_at = utc_now()
            return True

    def enqueue_all(self, priority: int = 100) -> int:
        count = 0
        for rel_path in sorted(self.videos_by_rel):
            if self.enqueue(rel_path, priority):
                count += 1
        return count

    def prioritize_episode(self, dataset: dict[str, Any], episode_index: int) -> int:
        videos = (dataset.get("videos_by_episode") or {}).get(episode_index, [])
        count = 0
        for video in videos:
            rel_path = str(video.get("rel_path") or "")
            if self.enqueue(rel_path, -100):
                count += 1
        with self.lock:
            if episode_index not in self.priority_episodes:
                self.priority_episodes = ([episode_index] + self.priority_episodes)[:20]
        return count

    def start(self) -> None:
        encoder, gpu_indices = choose_proxy_encoder()
        worker_count = proxy_worker_count(encoder, gpu_indices)
        with self.lock:
            self.encoder = encoder
            self.gpu_indices = gpu_indices
            self.worker_count = worker_count
            self.status = "running"
            self.updated_at = utc_now()
        for worker_index in range(worker_count):
            thread = threading.Thread(target=self.worker, args=(worker_index,), name=f"proxy-build-{self.dataset_id}-{worker_index}", daemon=True)
            thread.start()

    def worker(self, worker_index: int) -> None:
        while True:
            if self.cancel_requested:
                self.mark_cancelled()
                return
            try:
                priority, _sequence, rel_path = self.queue.get(timeout=1.0)
            except queue.Empty:
                with self.lock:
                    if len(self.done) >= self.total or (not self.queued_priorities and not self.in_progress and self.queue.empty()):
                        self.mark_complete_locked()
                        return
                continue
            with self.lock:
                current_priority = self.queued_priorities.get(rel_path)
                if current_priority is None or current_priority != priority:
                    self.queue.task_done()
                    continue
                self.queued_priorities.pop(rel_path, None)
                self.in_progress.add(rel_path)
                self.updated_at = utc_now()
            try:
                src = (self.dataset_path / proxy_rel_path(rel_path)).resolve()
                dst = proxy_output_path_for_rel(self.dataset_path, rel_path)
                encoder = self.encoder
                gpu_index = None
                if encoder == "h264_nvenc" and self.gpu_indices:
                    gpu_index = self.gpu_indices[worker_index % len(self.gpu_indices)]
                status, error = transcode_proxy_video(src, dst, encoder, gpu_index)
                if status == "failed" and encoder == "h264_nvenc" and PROXY_GPU_FALLBACK_CPU:
                    status, error = transcode_proxy_video(src, dst, "libx264", None)
            except Exception as exc:
                status, error = "failed", str(exc)
            with self.lock:
                self.in_progress.discard(rel_path)
                self.done.add(rel_path)
                if status == "built":
                    self.built += 1
                elif status == "skipped":
                    self.skipped += 1
                else:
                    self.failed += 1
                    if len(self.failed_items) < 50:
                        self.failed_items.append({"rel_path": rel_path, "error": error[:500]})
                self.updated_at = utc_now()
                if len(self.done) >= self.total and not self.in_progress and not self.queued_priorities:
                    self.mark_complete_locked()
            self.queue.task_done()

    def mark_complete_locked(self) -> None:
        if self.status in {"complete", "cancelled"}:
            return
        self.status = "complete"
        self.finished_at = utc_now()
        self.updated_at = self.finished_at
        append_proxy_build_history(self.history_record_locked("complete"))

    def mark_cancelled(self) -> None:
        with self.lock:
            if self.status == "cancelled":
                return
            self.status = "cancelled"
            self.finished_at = utc_now()
            self.updated_at = self.finished_at
            append_proxy_build_history(self.history_record_locked("cancelled"))

    def cancel(self) -> None:
        with self.lock:
            self.cancel_requested = True
            self.updated_at = utc_now()

    def payload_locked(self) -> dict[str, Any]:
        pending = len(self.queued_priorities)
        in_progress = len(self.in_progress)
        done = len(self.done)
        ended_at = self.finished_at or self.updated_at
        duration_seconds = proxy_build_duration_seconds(self.created_at, ended_at)
        return {
            "dataset_id": self.dataset_id,
            "dataset_path": str(self.dataset_path),
            "output_root": self.output_root,
            "status": self.status,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "duration_seconds": duration_seconds,
            "videos_per_second": proxy_build_rate(done, duration_seconds),
            "encoder": self.encoder,
            "gpu_indices": self.gpu_indices,
            "worker_count": self.worker_count,
            "total": self.total,
            "built": self.built,
            "skipped": self.skipped,
            "failed": self.failed,
            "pending": pending,
            "in_progress": in_progress,
            "done": done,
            "percent": round((done / self.total) * 100, 2) if self.total else 100.0,
            "priority_episodes": list(self.priority_episodes),
            "failed_items": list(self.failed_items),
            "config": {
                "auto": PROXY_BUILD_ON_DATASET_LOAD,
                "workers": PROXY_BUILD_WORKERS,
                "crf": PROXY_BUILD_CRF,
                "preset": PROXY_BUILD_PRESET,
                "encoder": PROXY_BUILD_ENCODER,
                "gpu_select_mode": PROXY_GPU_SELECT_MODE,
                "gpu_max_util": PROXY_GPU_MAX_UTIL,
                "gpu_max_memory_ratio": PROXY_GPU_MAX_MEMORY_RATIO,
                "gpu_memory_max_ratio": PROXY_GPU_MEMORY_MAX_RATIO,
                "gpu_workers_per_gpu": PROXY_GPU_WORKERS_PER_GPU,
                "gpu_fallback_cpu": PROXY_GPU_FALLBACK_CPU,
            },
        }

    def history_record_locked(self, event: str) -> dict[str, Any]:
        record = self.payload_locked()
        record.update(
            {
                "schema_version": 1,
                "event": event,
                "recorded_at": utc_now(),
            }
        )
        return record

    def payload(self) -> dict[str, Any]:
        with self.lock:
            return self.payload_locked()


def schedule_proxy_build(dataset_path: Path, dataset: dict[str, Any], reason: str = "load", force: bool = False) -> dict[str, Any]:
    if not PROXY_BUILD_ON_DATASET_LOAD and not force:
        return proxy_build_status(dataset_path)
    dataset_key = dataset_id(dataset_path)
    fingerprint = tuple(dataset.get("fingerprint") or ())
    with PROXY_BUILD_LOCK:
        existing = PROXY_BUILD_JOBS.get(dataset_key)
        if existing and not force:
            if existing.dataset_fingerprint == fingerprint and existing.status in {"queued", "running", "complete"}:
                return existing.payload()
        if existing and force:
            existing.cancel()
        job = ProxyBuildJob(dataset_path, dataset, reason=reason)
        PROXY_BUILD_JOBS[dataset_key] = job
        job.enqueue_all(priority=100)
        job.start()
        return job.payload()


def prioritize_proxy_episode(dataset_path: Path, dataset: dict[str, Any], episode_index: int) -> dict[str, Any]:
    if not PROXY_BUILD_ON_DATASET_LOAD:
        return proxy_build_status(dataset_path)
    dataset_key = dataset_id(dataset_path)
    with PROXY_BUILD_LOCK:
        job = PROXY_BUILD_JOBS.get(dataset_key)
    if not job:
        return schedule_proxy_build(dataset_path, dataset, reason="episode-priority")
    job.prioritize_episode(dataset, episode_index)
    return job.payload()


def cancel_proxy_build(dataset_path: Path) -> dict[str, Any]:
    dataset_key = dataset_id(dataset_path)
    with PROXY_BUILD_LOCK:
        job = PROXY_BUILD_JOBS.get(dataset_key)
    if job:
        job.cancel()
        return job.payload()
    return proxy_build_status(dataset_path)


def proxy_build_status(dataset_path: Path | None = None) -> dict[str, Any]:
    with PROXY_BUILD_LOCK:
        if dataset_path is not None:
            job = PROXY_BUILD_JOBS.get(dataset_id(dataset_path))
            return job.payload() if job else {
                "dataset_id": dataset_id(dataset_path),
                "dataset_path": str(dataset_path),
                "status": "idle",
                "auto": PROXY_BUILD_ON_DATASET_LOAD,
            }
        return {"jobs": [job.payload() for job in PROXY_BUILD_JOBS.values()]}


def proxy_build_history_payload(dataset_path: Path | None, limit: int) -> dict[str, Any]:
    rows = read_proxy_build_history(dataset_path, limit=limit)
    return {
        "history_path": str(PROXY_BUILD_HISTORY_PATH),
        "limit": limit,
        "count": len(rows),
        "history": rows,
    }


def safe_rel_media_path(raw_rel: str | None) -> PurePosixPath:
    if not raw_rel:
        raise AppError("Missing media path", 400)
    rel_path = PurePosixPath(unquote(raw_rel))
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise AppError("Invalid media path", 400)
    return rel_path


def proxy_video_path(dataset_path: Path, rel_path: str) -> Path:
    rel = safe_rel_media_path(rel_path)
    return proxy_output_path_for_rel(dataset_path, rel.as_posix())


def media_url(dataset_path: Path, rel_path: str) -> str:
    params = {
        "dataset": str(dataset_path),
        "rel": rel_path,
    }
    token = SERVER_CONFIG.get("token")
    if token:
        params["token"] = token
    endpoint = "/proxy_media" if proxy_video_path(dataset_path, rel_path).is_file() else "/media"
    return endpoint + "?" + urlencode(params)


def load_dataset(dataset_path: Path, refresh: bool = False) -> dict[str, Any]:
    info_path = dataset_path / "meta" / "info.json"
    episodes_path = dataset_path / "meta" / "episodes.jsonl"
    videos_root = dataset_path / "videos"
    data_root = dataset_path / "data"
    cache_key = str(dataset_path)
    fingerprint = (
        mtime_or_zero(info_path),
        mtime_or_zero(episodes_path),
        mtime_or_zero(videos_root),
        mtime_or_zero(data_root),
    )

    with DATASET_CACHE_LOCK:
        cached = DATASET_CACHE.get(cache_key)
        if cached and not refresh and cached.get("fingerprint") == fingerprint:
            return cached

    info = read_json(info_path, fallback={}) or {}
    tasks = read_jsonl(dataset_path / "meta" / "tasks.jsonl")
    episodes = read_jsonl(episodes_path)
    videos_by_episode = scan_videos(dataset_path)
    data_files = scan_data_files(dataset_path)

    normalized_episodes = []
    for episode in episodes:
        if "episode_index" not in episode:
            continue
        episode_index = int(episode["episode_index"])
        item = dict(episode)
        item["episode_index"] = episode_index
        item["episode_name"] = f"episode_{episode_index:06d}"
        item["data_rel_path"] = data_files.get(episode_index)
        item["video_count"] = len(videos_by_episode.get(episode_index, []))
        normalized_episodes.append(item)

    normalized_episodes.sort(key=lambda item: item["episode_index"])

    dataset = {
        "fingerprint": fingerprint,
        "dataset_path": str(dataset_path),
        "dataset_id": dataset_id(dataset_path),
        "info": info,
        "tasks": tasks,
        "episodes": normalized_episodes,
        "episode_by_index": {item["episode_index"]: item for item in normalized_episodes},
        "videos_by_episode": videos_by_episode,
    }

    with DATASET_CACHE_LOCK:
        DATASET_CACHE[cache_key] = dataset
    schedule_dataset_collector_prefetch(dataset_path, dataset)
    schedule_proxy_build(dataset_path, dataset, reason="load")
    return dataset


def nested_lookup(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [string_value(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("name", "username", "user", "identifier", "device_identifier", "code", "number", "id", "uid"):
            text = string_value(value.get(key))
            if text:
                return text
    return ""


def first_metadata_value(payload: Any, keys: tuple[str, ...], max_depth: int = 6) -> str:
    if max_depth < 0:
        return ""
    if isinstance(payload, dict):
        for key in keys:
            text = string_value(payload.get(key))
            if text:
                return text
        for value in payload.values():
            text = first_metadata_value(value, keys, max_depth - 1)
            if text:
                return text
    elif isinstance(payload, list):
        for item in payload:
            text = first_metadata_value(item, keys, max_depth - 1)
            if text:
                return text
    return ""


def first_metadata_path_value(payload: dict[str, Any], paths: list[tuple[str, ...]]) -> str:
    roots: list[Any] = [payload]
    for key in ("data", "result", "raw_metadata", "metadata", "extra"):
        value = payload.get(key)
        if isinstance(value, dict):
            roots.append(value)
            extra = value.get("extra")
            if isinstance(extra, dict):
                roots.append(extra)
    for root in roots:
        for path in paths:
            text = string_value(nested_lookup(root, path))
            if text:
                return text
    return ""


def raw_metadata_candidate_paths(episode_uuid: str) -> list[Path]:
    uuid = str(episode_uuid or "").strip().lower()
    if not uuid:
        return []
    return [root / uuid / "preprocessed" / "metadata.json" for root in configured_raw_episode_roots()]


def collector_from_metadata(metadata: dict[str, Any]) -> str:
    candidates = [
        ("collector",),
        ("collector_name",),
        ("collector_id",),
        ("collect_user",),
        ("collection_user",),
        ("collection_operator",),
        ("operator",),
        ("operator_name",),
        ("operator_id",),
        ("created_by",),
        ("creator",),
        ("creator_name",),
        ("author",),
        ("owner",),
        ("user",),
        ("username",),
        ("user_name",),
        ("???",),
        ("???",),
        ("metadata", "collector"),
        ("metadata", "operator"),
        ("metadata", "created_by"),
        ("metadata", "user"),
        ("extra", "user_name"),
        ("extra", "user_id"),
        ("extra", "collector"),
        ("extra", "operator"),
    ]
    for path in candidates:
        value = string_value(nested_lookup(metadata, path))
        if value:
            return value
    return first_metadata_value(
        metadata,
        (
            "collector",
            "collector_name",
            "collector_id",
            "collector_username",
            "collector_user_name",
            "collect_user",
            "collection_user",
            "collection_operator",
            "operator",
            "operator_name",
            "operator_id",
            "created_by",
            "creator",
            "creator_name",
            "author",
            "owner",
            "username",
            "user_name",
            "user_id",
        ),
    )


def metadata_json_text(metadata: Any) -> str:
    try:
        return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), default=json_default)[:20000]
    except (TypeError, ValueError):
        return ""


def metadata_task_from_metadata(metadata: dict[str, Any]) -> str:
    return first_metadata_path_value(
        metadata,
        [
            ("task_description",),
            ("task_annotation",),
            ("task_name",),
            ("task", "name"),
            ("task", "description"),
            ("metadata", "task_description"),
            ("metadata", "task_annotation"),
            ("extra", "task_description"),
            ("extra", "task_annotation"),
        ],
    ) or first_metadata_value(metadata, ("task_description", "task_annotation", "task_name"), max_depth=4)


def seat_from_metadata(metadata: dict[str, Any]) -> tuple[str, str]:
    seat = first_metadata_path_value(
        metadata,
        [
            ("seat",),
            ("seat_id",),
            ("seat_no",),
            ("seat_number",),
            ("station",),
            ("station_id",),
            ("station_number",),
            ("position",),
            ("metadata", "seat"),
            ("metadata", "seat_number"),
            ("metadata", "station"),
            ("extra", "seat"),
            ("extra", "seat_number"),
            ("extra", "station"),
        ],
    ) or first_metadata_value(
        metadata,
        ("seat", "seat_id", "seat_no", "seat_number", "station", "station_id", "station_number", "position"),
    )
    seat_number = first_metadata_path_value(
        metadata,
        [
            ("seat_number",),
            ("seat_no",),
            ("seat", "number"),
            ("station_number",),
            ("station_no",),
            ("metadata", "seat_number"),
            ("extra", "seat_number"),
        ],
    ) or seat
    return seat, seat_number


def device_from_metadata(metadata: dict[str, Any]) -> tuple[str, str, str]:
    device = first_metadata_path_value(
        metadata,
        [
            ("device",),
            ("devices",),
            ("device_name",),
            ("robot",),
            ("robot_name",),
            ("metadata", "device"),
            ("metadata", "devices"),
            ("extra", "device"),
            ("extra", "devices"),
        ],
    ) or first_metadata_value(metadata, ("device", "devices", "device_name", "robot", "robot_name"), max_depth=4)
    device_id = first_metadata_path_value(
        metadata,
        [
            ("device_id",),
            ("device_no",),
            ("device_number",),
            ("robot_id",),
            ("robot_no",),
            ("metadata", "device_id"),
            ("extra", "device_id"),
        ],
    ) or first_metadata_value(metadata, ("device_id", "device_no", "device_number", "robot_id", "robot_no"))
    device_identifier = first_metadata_path_value(
        metadata,
        [
            ("device_identifier",),
            ("identifier",),
            ("indentifier",),
            ("device", "identifier"),
            ("device", "indentifier"),
            ("robot", "identifier"),
            ("metadata", "device_identifier"),
            ("raw_metadata", "extra", "device"),
            ("extra", "device_identifier"),
            ("extra", "device"),
        ],
    ) or first_metadata_value(metadata, ("device_identifier", "identifier", "indentifier"), max_depth=5)
    return device, device_id, device_identifier


def source_metadata_from_payload(
    episode_uuid: str,
    metadata: dict[str, Any],
    *,
    metadata_path: str,
    raw_path: str = "",
) -> dict[str, Any]:
    seat, seat_number = seat_from_metadata(metadata)
    device, device_id, device_identifier = device_from_metadata(metadata)
    return {
        "episode_uuid": str(episode_uuid or "").strip().lower(),
        "collector": collector_from_metadata(metadata),
        "seat": seat,
        "seat_number": seat_number,
        "device": device,
        "device_id": device_id,
        "device_identifier": device_identifier,
        "task": metadata_task_from_metadata(metadata),
        "metadata_json": metadata_json_text(metadata),
        "raw_path": raw_path,
        "metadata_path": metadata_path,
        "raw_roots": source_metadata_signature(),
        "found": True,
    }


class DM3ApiError(RuntimeError):
    pass


def dm3_extract_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        data = response.get("data")
        if isinstance(data, dict):
            return data
        result = response.get("result")
        if isinstance(result, dict):
            return result
        return response
    return {}


def dm3_token_from_login_response(response: dict[str, Any]) -> str:
    token = string_value(response.get("token"))
    if token:
        return token
    data = response.get("data")
    if isinstance(data, dict):
        for key in ("token", "access_token", "accessToken"):
            token = string_value(data.get(key))
            if token:
                return token
    return ""


def dm3_http_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    auth_required: bool = True,
    refresh_on_unauthorized: bool = True,
) -> dict[str, Any]:
    global DM3_TOKEN
    if not dm3_enabled():
        raise DM3ApiError("DM3 is not configured")
    url = f"{DM3_BASE_URL}{path}"
    if params:
        url = f"{url}?{urlencode({key: value for key, value in params.items() if value is not None})}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth_required:
        token = dm3_get_token()
        if not token:
            raise DM3ApiError("DM3 token is empty")
        headers["Authorization"] = f"Bearer {token}"
    req = urlrequest.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=DM3_TIMEOUT_SECONDS) as response:
            raw_body = response.read()
    except urlerror.HTTPError as exc:
        if exc.code == 401 and auth_required and refresh_on_unauthorized and DM3_PHONE_NUMBER and DM3_PASSWORD:
            with DM3_TOKEN_LOCK:
                DM3_TOKEN = ""
            return dm3_http_json(
                method,
                path,
                params=params,
                payload=payload,
                auth_required=auth_required,
                refresh_on_unauthorized=False,
            )
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            error_body = ""
        raise DM3ApiError(f"DM3 HTTP {exc.code}: {error_body}") from exc
    except urlerror.URLError as exc:
        raise DM3ApiError(f"DM3 network error: {exc.reason}") from exc
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise DM3ApiError("DM3 response is not JSON") from exc
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def dm3_login() -> str:
    if DM3_STATIC_TOKEN:
        return DM3_STATIC_TOKEN
    if not DM3_PHONE_NUMBER or not DM3_PASSWORD:
        raise DM3ApiError("DM3 phone/password are not configured")
    response = dm3_http_json(
        "POST",
        "/api/v1/auth/login-by-phone",
        payload={"phone_number": DM3_PHONE_NUMBER, "password": DM3_PASSWORD},
        auth_required=False,
    )
    token = dm3_token_from_login_response(response)
    if not token:
        raise DM3ApiError("DM3 login response did not contain a token")
    return token


def dm3_get_token() -> str:
    global DM3_TOKEN
    if DM3_TOKEN:
        return DM3_TOKEN
    with DM3_TOKEN_LOCK:
        if not DM3_TOKEN:
            DM3_TOKEN = dm3_login()
        return DM3_TOKEN


def dm3_episode_metadata(episode_uuid: str) -> dict[str, Any]:
    uuid = str(episode_uuid or "").strip().lower()
    if not uuid or not dm3_enabled():
        return {}
    response = dm3_http_json("GET", "/api/v1/episode/metadata", params={"uuid": uuid})
    metadata = dm3_extract_payload(response)
    if not metadata:
        return {}
    return source_metadata_from_payload(
        uuid,
        metadata,
        metadata_path=f"dm3:/api/v1/episode/metadata?uuid={uuid}",
        raw_path="dm3:",
    )


def read_raw_metadata_with_timeout(metadata_path: Path) -> dict[str, Any] | None:
    code = r"""
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(2)
with path.open("r", encoding="utf-8-sig") as handle:
    payload = json.load(handle)
if not isinstance(payload, dict):
    payload = {}
print(json.dumps(payload, ensure_ascii=False))
"""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", code, str(metadata_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
    except OSError:
        return None
    deadline = time.monotonic() + RAW_METADATA_TIMEOUT_SECONDS
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        if proc.stdout:
            proc.stdout.close()
        return None
    stdout = proc.stdout.read() if proc.stdout else ""
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else {}


def raw_episode_metadata(episode_uuid: str) -> dict[str, Any]:
    uuid = str(episode_uuid or "").strip().lower()
    if not uuid:
        return {}
    with RAW_METADATA_LOCK:
        cached = RAW_METADATA_CACHE.get(uuid)
        if cached is not None and cached.get("found"):
            return dict(cached)

    result: dict[str, Any] = {
        "episode_uuid": uuid,
        "collector": "",
        "seat": "",
        "seat_number": "",
        "device": "",
        "device_id": "",
        "device_identifier": "",
        "task": "",
        "metadata_json": "",
        "raw_path": "",
        "metadata_path": "",
        "raw_roots": source_metadata_signature(),
        "found": False,
    }
    for metadata_path in raw_metadata_candidate_paths(uuid):
        metadata = read_raw_metadata_with_timeout(metadata_path)
        if metadata is None:
            continue
        result = source_metadata_from_payload(
            uuid,
            metadata,
            raw_path=str(metadata_path.parents[1]),
            metadata_path=str(metadata_path),
        )
        break

    if result.get("found"):
        with RAW_METADATA_LOCK:
            RAW_METADATA_CACHE[uuid] = dict(result)
    return result


def iso_age_seconds(value: str | None) -> float:
    if not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def collector_cache_row_to_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "episode_uuid": row["episode_uuid"],
        "collector": row["collector"],
        "seat": row["seat"],
        "seat_number": row["seat_number"],
        "device": row["device"],
        "device_id": row["device_id"],
        "device_identifier": row["device_identifier"],
        "task": row["task"],
        "metadata_json": row["metadata_json"],
        "raw_path": row["raw_path"],
        "metadata_path": row["metadata_path"],
        "raw_roots": row["raw_roots"],
        "found": bool(row["found"]),
        "status": row["status"],
        "attempts": int(row["attempts"] or 0),
        "last_error": row["last_error"] or "",
        "updated_at": row["updated_at"],
        "cached": True,
    }


def empty_collector_metadata(episode: dict[str, Any] | None, status: str = "queued") -> dict[str, Any]:
    return {
        "episode_uuid": str((episode or {}).get("episode_uuid") or "").strip().lower(),
        "collector": "",
        "seat": "",
        "seat_number": "",
        "device": "",
        "device_id": "",
        "device_identifier": "",
        "task": "",
        "metadata_json": "",
        "raw_path": "",
        "metadata_path": "",
        "raw_roots": source_metadata_signature(),
        "found": False,
        "status": status,
        "attempts": 0,
        "last_error": "",
        "updated_at": "",
        "cached": False,
    }


def collector_cache_should_fetch(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return True
    if payload.get("raw_roots") != source_metadata_signature():
        return True
    if payload.get("collector") or payload.get("status") == "fetched":
        return False
    if payload.get("status") in {"missing", "missing_collector", "error"}:
        return iso_age_seconds(payload.get("updated_at")) > COLLECTOR_CACHE_NEGATIVE_TTL_SECONDS
    return True


def collector_cache_get(dataset_path: Path, episode_index: int) -> dict[str, Any] | None:
    with connect_label_db(dataset_path) as conn:
        init_label_db(conn)
        row = conn.execute(
            """
            SELECT dataset_id, episode_index, dataset_path, episode_name, episode_uuid,
                   collector, seat, seat_number, device, device_id, device_identifier,
                   task, metadata_json, raw_path, metadata_path, raw_roots, found, status, attempts,
                   last_error, updated_at
            FROM collector_cache
            WHERE dataset_id = ? AND episode_index = ?
            """,
            (dataset_id(dataset_path), episode_index),
        ).fetchone()
    return collector_cache_row_to_payload(row) if row else None


def collector_cache_map(dataset_path: Path) -> dict[int, dict[str, Any]]:
    with connect_label_db(dataset_path) as conn:
        init_label_db(conn)
        rows = conn.execute(
            """
            SELECT dataset_id, episode_index, dataset_path, episode_name, episode_uuid,
                   collector, seat, seat_number, device, device_id, device_identifier,
                   task, metadata_json, raw_path, metadata_path, raw_roots, found, status, attempts,
                   last_error, updated_at
            FROM collector_cache
            WHERE dataset_id = ?
            """,
            (dataset_id(dataset_path),),
        ).fetchall()
    return {int(row["episode_index"]): collector_cache_row_to_payload(row) for row in rows}


def upsert_collector_cache(
    dataset_path: Path,
    episode: dict[str, Any],
    source_metadata: dict[str, Any],
    error: str = "",
) -> dict[str, Any]:
    episode_index = int(episode["episode_index"])
    episode_uuid = str(source_metadata.get("episode_uuid") or episode.get("episode_uuid") or "").strip().lower()
    found = bool(source_metadata.get("found"))
    collector = str(source_metadata.get("collector") or "").strip()
    seat = str(source_metadata.get("seat") or "").strip()
    seat_number = str(source_metadata.get("seat_number") or "").strip()
    device = str(source_metadata.get("device") or "").strip()
    device_id_value = str(source_metadata.get("device_id") or "").strip()
    device_identifier = str(source_metadata.get("device_identifier") or "").strip()
    task = str(source_metadata.get("task") or "").strip()
    metadata_json = str(source_metadata.get("metadata_json") or "")
    status = "error" if error else ("fetched" if found and collector else ("missing_collector" if found else "missing"))
    now = utc_now()
    with connect_label_db(dataset_path) as conn:
        init_label_db(conn)
        row = conn.execute(
            """
            SELECT attempts FROM collector_cache
            WHERE dataset_id = ? AND episode_index = ?
            """,
            (dataset_id(dataset_path), episode_index),
        ).fetchone()
        attempts = int(row["attempts"] or 0) + 1 if row else 1
        conn.execute(
            """
            INSERT INTO collector_cache (
                dataset_id, episode_index, dataset_path, episode_name, episode_uuid,
                collector, seat, seat_number, device, device_id, device_identifier, task,
                metadata_json, raw_path, metadata_path, raw_roots, found, status, attempts,
                last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id, episode_index) DO UPDATE SET
                dataset_path = excluded.dataset_path,
                episode_name = excluded.episode_name,
                episode_uuid = excluded.episode_uuid,
                collector = excluded.collector,
                seat = excluded.seat,
                seat_number = excluded.seat_number,
                device = excluded.device,
                device_id = excluded.device_id,
                device_identifier = excluded.device_identifier,
                task = excluded.task,
                metadata_json = excluded.metadata_json,
                raw_path = excluded.raw_path,
                metadata_path = excluded.metadata_path,
                raw_roots = excluded.raw_roots,
                found = excluded.found,
                status = excluded.status,
                attempts = excluded.attempts,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                dataset_id(dataset_path),
                episode_index,
                str(dataset_path),
                episode.get("episode_name", f"episode_{episode_index:06d}"),
                episode_uuid,
                collector,
                seat,
                seat_number,
                device,
                device_id_value,
                device_identifier,
                task,
                metadata_json,
                str(source_metadata.get("raw_path") or ""),
                str(source_metadata.get("metadata_path") or ""),
                str(source_metadata.get("raw_roots") or source_metadata_signature()),
                1 if found else 0,
                status,
                attempts,
                error[:500],
                now,
            ),
        )
        conn.commit()
    return {
        "episode_uuid": episode_uuid,
        "collector": collector,
        "seat": seat,
        "seat_number": seat_number,
        "device": device,
        "device_id": device_id_value,
        "device_identifier": device_identifier,
        "task": task,
        "metadata_json": metadata_json,
        "raw_path": str(source_metadata.get("raw_path") or ""),
        "metadata_path": str(source_metadata.get("metadata_path") or ""),
        "raw_roots": str(source_metadata.get("raw_roots") or source_metadata_signature()),
        "found": found,
        "status": status,
        "attempts": attempts,
        "last_error": error[:500],
        "updated_at": now,
        "cached": True,
    }


def fetch_and_store_collector_metadata(dataset_path: Path, episode: dict[str, Any]) -> dict[str, Any]:
    episode_uuid = str(episode.get("episode_uuid") or "").strip().lower()
    if not episode_uuid:
        return empty_collector_metadata(episode, status="missing")
    errors: list[str] = []
    try:
        source_metadata: dict[str, Any] = {}
        if dm3_enabled():
            try:
                source_metadata = dm3_episode_metadata(episode_uuid)
            except Exception as exc:
                errors.append(str(exc))
        if not source_metadata and raw_metadata_fallback_enabled():
            try:
                source_metadata = raw_episode_metadata(episode_uuid)
            except Exception as exc:
                errors.append(str(exc))
        if not source_metadata:
            source_metadata = empty_collector_metadata(episode, status="missing")
        return upsert_collector_cache(
            dataset_path,
            episode,
            source_metadata,
            "; ".join(errors)[:500] if errors and not source_metadata.get("found") else "",
        )
    except Exception as exc:
        return upsert_collector_cache(dataset_path, episode, empty_collector_metadata(episode, status="error"), str(exc))


COLLECTOR_WORKERS_STARTED = False


def ensure_collector_cache_workers() -> None:
    global COLLECTOR_WORKERS_STARTED
    with COLLECTOR_CACHE_LOCK:
        if COLLECTOR_WORKERS_STARTED:
            return
        COLLECTOR_WORKERS_STARTED = True
    for index in range(COLLECTOR_CACHE_WORKERS):
        worker = threading.Thread(target=collector_cache_worker, name=f"collector-cache-{index + 1}", daemon=True)
        worker.start()


def schedule_collector_fetch(dataset_path: Path, episode: dict[str, Any], priority: int = 50) -> bool:
    episode_uuid = str(episode.get("episode_uuid") or "").strip().lower()
    if not episode_uuid:
        return False
    episode_index = int(episode["episode_index"])
    key = (dataset_id(dataset_path), episode_index)
    global COLLECTOR_QUEUE_SEQUENCE
    ensure_collector_cache_workers()
    with COLLECTOR_CACHE_LOCK:
        existing_priority = COLLECTOR_CACHE_PENDING.get(key)
        if existing_priority is not None and existing_priority <= priority:
            return False
        COLLECTOR_CACHE_PENDING[key] = priority
        COLLECTOR_QUEUE_SEQUENCE += 1
        sequence = COLLECTOR_QUEUE_SEQUENCE
    COLLECTOR_CACHE_QUEUE.put(
        (
            priority,
            sequence,
            str(dataset_path),
            episode.get("episode_name", f"episode_{episode_index:06d}"),
            episode_index,
            episode_uuid,
        )
    )
    return True


def collector_cache_worker() -> None:
    while True:
        priority, _sequence, raw_dataset_path, episode_name, episode_index, episode_uuid = COLLECTOR_CACHE_QUEUE.get()
        dataset_path = Path(raw_dataset_path)
        key = (dataset_id(dataset_path), episode_index)
        try:
            with COLLECTOR_CACHE_LOCK:
                current_priority = COLLECTOR_CACHE_PENDING.get(key)
                if current_priority is None or current_priority != priority:
                    continue
                COLLECTOR_CACHE_PENDING.pop(key, None)
            cached = collector_cache_get(dataset_path, episode_index)
            if not collector_cache_should_fetch(cached):
                continue
            fetch_and_store_collector_metadata(
                dataset_path,
                {
                    "episode_index": episode_index,
                    "episode_name": episode_name,
                    "episode_uuid": episode_uuid,
                },
            )
        except Exception:
            pass
        finally:
            COLLECTOR_CACHE_QUEUE.task_done()


def schedule_dataset_collector_prefetch(dataset_path: Path, dataset: dict[str, Any]) -> None:
    fingerprint = tuple(dataset.get("fingerprint") or ())
    prefetch_key = (dataset_id(dataset_path), fingerprint, source_metadata_signature())
    with COLLECTOR_CACHE_LOCK:
        if prefetch_key in COLLECTOR_PREFETCH_KEYS:
            return
        COLLECTOR_PREFETCH_KEYS.add(prefetch_key)
    try:
        cached_by_index = collector_cache_map(dataset_path)
    except Exception:
        cached_by_index = {}
    for episode in dataset.get("episodes") or []:
        try:
            episode_index = int(episode["episode_index"])
        except (KeyError, TypeError, ValueError):
            continue
        cached = cached_by_index.get(episode_index)
        if collector_cache_should_fetch(cached):
            schedule_collector_fetch(dataset_path, episode, priority=50)


def cached_or_queued_source_metadata(dataset_path: Path, episode: dict[str, Any], priority: int = 5) -> dict[str, Any]:
    cached = collector_cache_get(dataset_path, int(episode["episode_index"]))
    if cached and not collector_cache_should_fetch(cached):
        return cached
    schedule_collector_fetch(dataset_path, episode, priority=priority)
    return cached or empty_collector_metadata(episode, status="queued")


def source_metadata_for_episode(dataset_path: Path, episode: dict[str, Any] | None, episode_uuid: str = "") -> dict[str, Any]:
    if episode is None:
        uuid = str(episode_uuid or "").strip().lower()
        if not uuid:
            return {}
        if dm3_enabled():
            try:
                metadata = dm3_episode_metadata(uuid)
                if metadata:
                    return metadata
            except Exception:
                if not raw_metadata_fallback_enabled():
                    return empty_collector_metadata({"episode_uuid": uuid}, status="error")
        return raw_episode_metadata(uuid) if raw_metadata_fallback_enabled() else empty_collector_metadata({"episode_uuid": uuid}, status="missing")
    cached = collector_cache_get(dataset_path, int(episode["episode_index"]))
    if cached and not collector_cache_should_fetch(cached):
        return cached
    return fetch_and_store_collector_metadata(dataset_path, episode)


def collector_cache_summary(
    dataset_path: Path,
    dataset: dict[str, Any],
    cached_by_index: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cached_by_index = cached_by_index if cached_by_index is not None else collector_cache_map(dataset_path)
    total_with_uuid = sum(1 for episode in dataset.get("episodes") or [] if str(episode.get("episode_uuid") or "").strip())
    known = sum(1 for payload in cached_by_index.values() if str(payload.get("collector") or "").strip())
    found = sum(1 for payload in cached_by_index.values() if payload.get("found"))
    with COLLECTOR_CACHE_LOCK:
        queued = sum(1 for key in COLLECTOR_CACHE_PENDING if key[0] == dataset_id(dataset_path))
    return {
        "total": total_with_uuid,
        "cached": len(cached_by_index),
        "found": found,
        "known": known,
        "unknown": max(0, total_with_uuid - known),
        "queued": queued,
    }


def status_counts(dataset: dict[str, Any], store: dict[str, Any], user: str) -> dict[str, int]:
    counts = {"total": len(dataset["episodes"]), "reject": 0, "pending": 0, "accept": 0}
    global_marked_indices = set()
    global_label_map = store.get("labels") or {}
    for key, label in global_label_map.items():
        if review_status(label.get("status")) in RECORDED_STATUS_VALUES:
            try:
                global_marked_indices.add(int(key))
            except ValueError:
                continue
    user_marked_indices = set()
    user_labels = (store.get("labels_by_user") or {}).get(user) or {}
    if isinstance(user_labels, dict):
        for key, label in user_labels.items():
            if isinstance(label, dict) and review_status(label.get("status")) in RECORDED_STATUS_VALUES:
                try:
                    user_marked_indices.add(int(key))
                except ValueError:
                    continue

    for episode in dataset["episodes"]:
        episode_index = int(episode["episode_index"])
        status = review_status(label_for_episode(store, user, episode_index).get("status"))
        counts[status] += 1
    counts["marked"] = len(user_marked_indices)
    counts["all_marked"] = len(global_marked_indices)
    return counts


def status_counts_from_label_map(dataset: dict[str, Any], labels: dict[str, Any]) -> dict[str, int]:
    counts = {"total": len(dataset["episodes"]), "reject": 0, "pending": 0, "accept": 0}
    marked = 0
    for episode in dataset["episodes"]:
        key = str(int(episode["episode_index"]))
        label = labels.get(key)
        status = review_status(label.get("status")) if isinstance(label, dict) else "pending"
        counts[status] += 1
        if isinstance(label, dict) and status in RECORDED_STATUS_VALUES:
            marked += 1
    counts["marked"] = marked
    counts["all_marked"] = marked
    return counts


def user_summaries(dataset: dict[str, Any], store: dict[str, Any]) -> list[dict[str, Any]]:
    users = []
    for user, labels in sorted((store.get("labels_by_user") or {}).items()):
        if not isinstance(labels, dict):
            continue
        counts = status_counts_from_label_map(dataset, labels)
        users.append({"user": user, "counts": counts})
    return users


def label_event_user_summaries(dataset_path: Path) -> dict[str, dict[str, Any]]:
    with connect_label_db(dataset_path) as conn:
        init_label_db(conn)
        import_json_labels_if_needed(dataset_path, conn)
        rows = conn.execute(
            """
            SELECT user, COUNT(*) AS event_count, MAX(created_at) AS last_event_at
            FROM label_events
            WHERE dataset_id = ?
            GROUP BY user
            """,
            (dataset_id(dataset_path),),
        ).fetchall()
    return {
        row["user"]: {
            "event_count": int(row["event_count"] or 0),
            "last_event_at": row["last_event_at"] or "",
        }
        for row in rows
    }


def admin_payload(dataset_path: Path, dataset: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    current_users = {item["user"]: item for item in user_summaries(dataset, store)}
    event_users = label_event_user_summaries(dataset_path)
    users = []
    for user in sorted(set(current_users) | set(event_users)):
        current = current_users.get(user) or {
            "user": user,
            "counts": {"total": len(dataset["episodes"]), "reject": 0, "pending": len(dataset["episodes"]), "accept": 0, "marked": 0, "all_marked": 0},
        }
        users.append({
            "user": user,
            "counts": current["counts"],
            **event_users.get(user, {"event_count": 0, "last_event_at": ""}),
        })

    labels = list((store.get("labels") or {}).values())
    recent_labels = sorted(labels, key=lambda item: str(item.get("updated_at", "")), reverse=True)[:80]
    presence = presence_snapshot(dataset_path, None)
    active = [
        {"episode_index": episode_index, "episode_name": f"episode_{episode_index:06d}", "users": users}
        for episode_index, users in sorted(presence.items())
    ]
    return {
        "dataset_path": str(dataset_path),
        "dataset_source": dataset_source_for_path(dataset_path),
        "dataset_id": dataset_id(dataset_path),
        "generated_at": utc_now(),
        "counts": status_counts_from_label_map(dataset, store.get("labels") or {}),
        "users": users,
        "recent_labels": recent_labels,
        "active": active,
        "paths": {
            "labels_db": str(labels_db_path(dataset_path)),
            "labels_json": str(labels_path(dataset_path)),
            "labels_jsonl": str(labels_jsonl_path(dataset_path)),
        },
    }


def rank_payload(dataset_path: Path, dataset: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    event_users = label_event_user_summaries(dataset_path)
    users = []
    for item in user_summaries(dataset, store):
        user = item["user"]
        counts = item.get("counts") or {}
        marked = int(counts.get("marked") or 0)
        reject = int(counts.get("reject") or 0)
        accept = int(counts.get("accept") or 0)
        pending = int(counts.get("pending") or 0)
        reject_rate = reject / marked if marked else 0
        users.append({
            "user": user,
            "marked": marked,
            "reject": reject,
            "accept": accept,
            "pending": pending,
            "reject_rate": reject_rate,
            **event_users.get(user, {"event_count": 0, "last_event_at": ""}),
        })
    for user, event_summary in event_users.items():
        if any(item["user"] == user for item in users):
            continue
        users.append({
            "user": user,
            "marked": 0,
            "reject": 0,
            "accept": 0,
            "pending": 0,
            "reject_rate": 0,
            **event_summary,
        })

    by_marked = sorted(
        users,
        key=lambda item: (-int(item.get("marked") or 0), -int(item.get("reject") or 0), str(item.get("user") or "")),
    )
    by_reject_rate = sorted(
        users,
        key=lambda item: (
            -float(item.get("reject_rate") or 0),
            -int(item.get("marked") or 0),
            str(item.get("user") or ""),
        ),
    )
    cached_collectors = collector_cache_map(dataset_path)
    global_label_map = store.get("labels") or {}
    unlabeled_episodes: list[dict[str, Any]] = []
    collector_stats: dict[str, dict[str, Any]] = {}
    for episode in dataset.get("episodes") or []:
        episode_index = int(episode["episode_index"])
        label = global_label_map.get(str(episode_index))
        if not isinstance(label, dict):
            unlabeled_episodes.append(
                {
                    "episode_index": episode_index,
                    "episode_name": episode.get("episode_name", f"episode_{episode_index:06d}"),
                    "episode_uuid": episode.get("episode_uuid", ""),
                    "task_description": episode.get("task_description", ""),
                    "task_annotation": episode.get("task_annotation", ""),
                }
            )
            continue
        status = review_status(label.get("status"))
        if status not in RECORDED_STATUS_VALUES:
            continue
        cached = cached_collectors.get(episode_index)
        collector = str((cached or {}).get("collector") or "").strip()
        if not collector:
            schedule_collector_fetch(dataset_path, episode, priority=20)
            collector = "?????"
        stat = collector_stats.setdefault(
            collector,
            {
                "collector": collector,
                "marked": 0,
                "reject": 0,
                "accept": 0,
                "pending": 0,
                "known": collector != "?????",
                "rejected_episodes": [],
            },
        )
        stat["marked"] += 1
        stat[status] += 1
        if status == "reject" and len(stat["rejected_episodes"]) < 200:
            stat["rejected_episodes"].append(
                {
                    "episode_index": episode_index,
                    "episode_name": episode.get("episode_name", f"episode_{episode_index:06d}"),
                    "episode_uuid": episode.get("episode_uuid", ""),
                    "user": label.get("user") or label.get("annotator") or "",
                    "updated_at": label.get("updated_at", ""),
                    "task_description": episode.get("task_description", ""),
                    "task_annotation": episode.get("task_annotation", ""),
                }
            )
    collectors = []
    for stat in collector_stats.values():
        marked = int(stat.get("marked") or 0)
        reject = int(stat.get("reject") or 0)
        stat["reject_rate"] = reject / marked if marked else 0
        stat["rejected_episodes"].sort(key=lambda item: int(item.get("episode_index") or 0))
        collectors.append(stat)
    collector_reject_rate = sorted(
        collectors,
        key=lambda item: (
            0 if item.get("known") else 1,
            -float(item.get("reject_rate") or 0),
            -int(item.get("marked") or 0),
            str(item.get("collector") or ""),
        ),
    )
    return {
        "dataset_path": str(dataset_path),
        "dataset_source": dataset_source_for_path(dataset_path),
        "dataset_id": dataset_id(dataset_path),
        "generated_at": utc_now(),
        "counts": status_counts_from_label_map(dataset, global_label_map),
        "collector_cache": collector_cache_summary(dataset_path, dataset, cached_collectors),
        "unlabeled_count": len(unlabeled_episodes),
        "unlabeled_episodes": unlabeled_episodes,
        "users": users,
        "rankings": {
            "marked": by_marked,
            "reject_rate": by_reject_rate,
            "collector_reject_rate": collector_reject_rate,
        },
    }


def compact_episode(
    dataset_path: Path,
    episode: dict[str, Any],
    label: dict[str, Any],
    store: dict[str, Any],
    locked_by: list[str] | None = None,
) -> dict[str, Any]:
    episode_index = int(episode["episode_index"])
    label_summary = per_episode_label_summary(store, episode_index)
    source = dataset_source_for_path(dataset_path)
    return {
        "dataset_id": dataset_id(dataset_path),
        "dataset_source": source,
        "dataset_name": dataset_source_name(source, dataset_path),
        "episode_index": episode_index,
        "episode_name": episode.get("episode_name", f"episode_{episode_index:06d}"),
        "episode_uuid": episode.get("episode_uuid", ""),
        "length": episode.get("length"),
        "tasks": episode.get("tasks") or [],
        "task_description": episode.get("task_description", ""),
        "task_annotation": episode.get("task_annotation", ""),
        "status": review_status(label.get("status")),
        "issues": label.get("issues", []),
        "has_note": bool((label.get("note") or "").strip()),
        "label_count": label_summary["label_count"],
        "label_users": label_summary["users"],
        "all_statuses": label_summary["statuses"],
        "effective_label": label_summary["effective_label"],
        "locked_by": locked_by or [],
        "video_count": episode.get("video_count", 0),
        "data_rel_path": episode.get("data_rel_path"),
    }


def full_episode(
    dataset_path: Path,
    dataset: dict[str, Any],
    episode_index: int,
    store: dict[str, Any],
    user: str,
) -> dict[str, Any]:
    episode = dataset["episode_by_index"].get(episode_index)
    if episode is None:
        raise AppError(f"Episode not found: {episode_index}", 404)
    prioritize_proxy_episode(dataset_path, dataset, episode_index)
    videos = []
    for video in dataset["videos_by_episode"].get(episode_index, []):
        item = dict(video)
        item["url"] = media_url(dataset_path, video["rel_path"])
        videos.append(item)
    label = label_for_episode(store, user, episode_index)
    locked_by = presence_snapshot(dataset_path, user).get(episode_index, [])
    source_metadata = cached_or_queued_source_metadata(dataset_path, episode, priority=10)
    source = dataset_source_for_path(dataset_path)
    return {
        "dataset_id": dataset_id(dataset_path),
        "dataset_source": source,
        "dataset_name": dataset_source_name(source, dataset_path),
        "episode": episode,
        "summary": compact_episode(dataset_path, episode, label, store, locked_by),
        "videos": videos,
        "label": label,
        "active_users": locked_by,
        "source_metadata": source_metadata,
        "user": user,
    }


def point_from_pose(value: Any) -> list[float | None]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return [clean_float(value[0]), clean_float(value[1]), clean_float(value[2])]
    return [None, None, None]


def point_from_state(state: Any, offset: int) -> list[float | None]:
    if isinstance(state, (list, tuple)) and len(state) >= offset + 3:
        return [clean_float(state[offset]), clean_float(state[offset + 1]), clean_float(state[offset + 2])]
    return [None, None, None]


def gripper_from_state(state: Any, offset: int) -> float | None:
    if isinstance(state, (list, tuple)) and len(state) > offset:
        return clean_float(state[offset])
    return None


def quat_from_state(state: Any, offset: int) -> list[float | None]:
    if isinstance(state, (list, tuple)) and len(state) >= offset + 4:
        return [
            clean_float(state[offset]),
            clean_float(state[offset + 1]),
            clean_float(state[offset + 2]),
            clean_float(state[offset + 3]),
        ]
    return [None, None, None, None]


def quat_from_pose(value: Any) -> list[float | None]:
    if isinstance(value, (list, tuple)) and len(value) >= 7:
        return [
            clean_float(value[3]),
            clean_float(value[4]),
            clean_float(value[5]),
            clean_float(value[6]),
        ]
    return [None, None, None, None]


def trajectory_metadata_for_episode(dataset: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    info = dataset.get("info") or {}
    device_type = string_value(episode.get("device_type") or info.get("device_type"))
    collection_mode = string_value(episode.get("collection_mode") or info.get("collection_mode"))
    device_type_lower = device_type.lower()
    collection_mode_lower = collection_mode.lower()
    is_teleop = (
        "teleoperation" in device_type_lower
        or device_type_lower in {"inference_r1", "rollout"}
        or "teleoperation" in collection_mode_lower
    )

    return {
        "device_type": device_type,
        "collection_mode": collection_mode,
        "transform": "teleop_rx_minus_90" if is_teleop else "identity",
        "world_up_axis": "y",
    }


def teleop_point_to_robopocket(point: list[float | None]) -> list[float | None]:
    if len(point) < 3 or any(value is None for value in point[:3]):
        return point
    x, y, z = point[:3]
    return [x, z, -y]


def quat_multiply(a: list[float], b: list[float]) -> list[float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]


def normalize_quat_values(quat: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in quat))
    if not math.isfinite(length) or length <= 1e-9:
        return quat
    return [value / length for value in quat]


def teleop_quat_to_robopocket(quat: list[float | None]) -> list[float | None]:
    if len(quat) < 4 or any(value is None for value in quat[:4]):
        return quat
    rotation = [math.sqrt(0.5), -math.sqrt(0.5), 0.0, 0.0]
    return normalize_quat_values(quat_multiply(rotation, [float(value) for value in quat[:4]]))


def update_ranges(ranges: dict[str, list[float | None]], point: list[float | None]) -> None:
    for axis, value in zip(("x", "y", "z"), point):
        if value is None:
            continue
        current = ranges[axis]
        if current[0] is None or value < current[0]:
            current[0] = value
        if current[1] is None or value > current[1]:
            current[1] = value


def load_trajectory(dataset_path: Path, dataset: dict[str, Any], episode_index: int, max_points: int = 900) -> dict[str, Any]:
    episode = dataset["episode_by_index"].get(episode_index)
    if episode is None:
        raise AppError(f"Episode not found: {episode_index}", 404)
    rel_path = episode.get("data_rel_path")
    if not rel_path:
        raise AppError(f"Episode has no parquet data: {episode_index}", 404)
    parquet_path = (dataset_path / rel_path).resolve()
    if not parquet_path.is_file():
        raise AppError(f"Parquet file not found: {rel_path}", 404)

    max_points = max(100, min(2000, max_points))
    cache_key = (str(dataset_path), episode_index, max_points, mtime_or_zero(parquet_path))
    cached = TRAJECTORY_CACHE.get(cache_key)
    if cached:
        return cached

    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise AppError(f"pyarrow is required to read parquet trajectories: {exc}", 500)

    desired_columns = [
        "timestamp",
        "frame_index",
        "observation.state",
        "action",
        "observation.extra.left.raw_pose",
        "observation.extra.right.raw_pose",
        "observation.extra.ego.raw_pose",
        "observation.extra.left.hand_state",
        "observation.extra.right.hand_state",
        "observation.extra.left.raw_hand_state",
        "observation.extra.right.raw_hand_state",
        "slam_diagnostics_valid_mask.left",
        "slam_diagnostics_valid_mask.right",
        "slam_diagnostics_valid_mask.ego",
        "width_valid_mask.left",
        "width_valid_mask.right",
    ]
    parquet_file = pq.ParquetFile(parquet_path)
    available = set(parquet_file.schema_arrow.names)
    columns = [name for name in desired_columns if name in available]
    table = pq.read_table(parquet_path, columns=columns)
    rows = table.to_pylist()
    total_rows = len(rows)
    stride = max(1, math.ceil(total_rows / max_points))

    frames: list[int] = []
    timestamps: list[float | None] = []
    left_points: list[list[float | None]] = []
    right_points: list[list[float | None]] = []
    ego_points: list[list[float | None]] = []
    left_quats: list[list[float | None]] = []
    right_quats: list[list[float | None]] = []
    ego_quats: list[list[float | None]] = []
    action_left_points: list[list[float | None]] = []
    action_right_points: list[list[float | None]] = []
    action_left_quats: list[list[float | None]] = []
    action_right_quats: list[list[float | None]] = []
    left_gripper: list[float | None] = []
    right_gripper: list[float | None] = []
    masks = {"left": [], "right": [], "ego": []}
    ranges: dict[str, list[float | None]] = {"x": [None, None], "y": [None, None], "z": [None, None]}
    trajectory_metadata = trajectory_metadata_for_episode(dataset, episode)
    transform_teleop = trajectory_metadata.get("transform") == "teleop_rx_minus_90"

    for row_index, row in enumerate(rows[::stride]):
        state = row.get("observation.state")
        action = row.get("action")
        frame = row.get("frame_index")
        frames.append(int(frame) if frame is not None else row_index * stride)
        timestamps.append(clean_float(row.get("timestamp")))

        left = point_from_state(state, 0)
        right = point_from_state(state, 8)
        ego = point_from_state(state, 16)
        left_quat = quat_from_state(state, 3)
        right_quat = quat_from_state(state, 11)
        ego_quat = quat_from_state(state, 19)
        action_left = point_from_state(action, 0)
        action_right = point_from_state(action, 8)
        action_left_quat = quat_from_state(action, 3)
        action_right_quat = quat_from_state(action, 11)
        if left[0] is None:
            left = point_from_pose(row.get("observation.extra.left.raw_pose"))
            left_quat = quat_from_pose(row.get("observation.extra.left.raw_pose"))
        if right[0] is None:
            right = point_from_pose(row.get("observation.extra.right.raw_pose"))
            right_quat = quat_from_pose(row.get("observation.extra.right.raw_pose"))
        if ego[0] is None:
            ego = point_from_pose(row.get("observation.extra.ego.raw_pose"))
            ego_quat = quat_from_pose(row.get("observation.extra.ego.raw_pose"))

        if transform_teleop:
            left = teleop_point_to_robopocket(left)
            right = teleop_point_to_robopocket(right)
            ego = teleop_point_to_robopocket(ego)
            action_left = teleop_point_to_robopocket(action_left)
            action_right = teleop_point_to_robopocket(action_right)
            left_quat = teleop_quat_to_robopocket(left_quat)
            right_quat = teleop_quat_to_robopocket(right_quat)
            ego_quat = teleop_quat_to_robopocket(ego_quat)
            action_left_quat = teleop_quat_to_robopocket(action_left_quat)
            action_right_quat = teleop_quat_to_robopocket(action_right_quat)

        left_points.append(left)
        right_points.append(right)
        ego_points.append(ego)
        left_quats.append(left_quat)
        right_quats.append(right_quat)
        ego_quats.append(ego_quat)
        action_left_points.append(action_left)
        action_right_points.append(action_right)
        action_left_quats.append(action_left_quat)
        action_right_quats.append(action_right_quat)
        update_ranges(ranges, left)
        update_ranges(ranges, right)
        update_ranges(ranges, ego)
        update_ranges(ranges, action_left)
        update_ranges(ranges, action_right)

        left_hand = clean_float(row.get("observation.extra.left.hand_state"))
        right_hand = clean_float(row.get("observation.extra.right.hand_state"))
        if left_hand is None:
            left_hand = gripper_from_state(state, 7)
        if right_hand is None:
            right_hand = gripper_from_state(state, 15)
        left_gripper.append(left_hand)
        right_gripper.append(right_hand)

        masks["left"].append(row.get("slam_diagnostics_valid_mask.left"))
        masks["right"].append(row.get("slam_diagnostics_valid_mask.right"))
        masks["ego"].append(row.get("slam_diagnostics_valid_mask.ego"))

    payload = {
        "episode_index": episode_index,
        "episode_name": episode.get("episode_name", f"episode_{episode_index:06d}"),
        "source": rel_path,
        "total_rows": total_rows,
        "stride": stride,
        "device_type": trajectory_metadata["device_type"],
        "world_up_axis": trajectory_metadata["world_up_axis"],
        "metadata": trajectory_metadata,
        "frames": frames,
        "timestamps": timestamps,
        "left": {"points": left_points, "quaternions": left_quats, "gripper": left_gripper},
        "right": {"points": right_points, "quaternions": right_quats, "gripper": right_gripper},
        "action": {
            "left": {"points": action_left_points, "quaternions": action_left_quats},
            "right": {"points": action_right_points, "quaternions": action_right_quats},
        },
        "ego": {"points": ego_points, "quaternions": ego_quats},
        "ranges": ranges,
        "masks": masks,
    }

    if len(TRAJECTORY_CACHE) > 128:
        TRAJECTORY_CACHE.clear()
    TRAJECTORY_CACHE[cache_key] = payload
    return payload


def query_value(query: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
    values = query.get(name)
    if not values:
        return default
    return values[0]


def parse_int(value: str | None, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def fuzzy_match_text(haystack: str, needle: str) -> bool:
    if not needle:
        return True
    if needle in haystack:
        return True
    cursor = 0
    for char in needle:
        cursor = haystack.find(char, cursor)
        if cursor < 0:
            return False
        cursor += 1
    return True


def episode_search_haystack(episode: dict[str, Any]) -> str:
    return " ".join(
        [
            str(episode.get("episode_index", "")),
            episode.get("episode_name", ""),
            episode.get("episode_uuid", ""),
            episode.get("task_description", ""),
            episode.get("task_annotation", ""),
            " ".join(episode.get("tasks") or []),
        ]
    ).lower()


def find_episode_position(dataset: dict[str, Any], search_text: str) -> int | None:
    query = search_text.strip().lower()
    if not query:
        return None

    episodes = dataset["episodes"]
    for position, episode in enumerate(episodes):
        if query == str(episode.get("episode_name", "")).lower():
            return position

    number_match = re.search(r"\d+", query)
    if number_match:
        target_index = int(number_match.group(0))
        for position, episode in enumerate(episodes):
            if int(episode["episode_index"]) == target_index:
                return position

    for position, episode in enumerate(episodes):
        if query == str(episode.get("episode_uuid", "")).lower():
            return position

    search_tokens = [token for token in re.split(r"\s+", query) if token]
    for position, episode in enumerate(episodes):
        haystack = episode_search_haystack(episode)
        if all(fuzzy_match_text(haystack, token) for token in search_tokens):
            return position
    return None


def episode_lookup_payload(
    dataset_path: Path,
    dataset: dict[str, Any],
    store: dict[str, Any],
    user: str,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    search_text = (query_value(query, "q", "") or "").strip()
    page_size = parse_int(query_value(query, "page_size"), 60, 1, 200)
    status_filter = query_value(query, "status", "all") or "all"
    if status_filter == "unlabeled":
        status_filter = "pending"

    if status_filter != "all":
        search_tokens = [token for token in re.split(r"\s+", search_text.lower()) if token]
        locks = presence_snapshot(dataset_path, user)
        filtered_count = 0
        match_payload = None
        match_position = None
        for episode in dataset["episodes"]:
            episode_index = int(episode["episode_index"])
            label = label_for_episode(store, user, episode_index)
            if review_status(label.get("status")) != status_filter:
                continue
            filtered_count += 1
            haystack = episode_search_haystack(episode)
            if match_payload is None and (not search_tokens or all(fuzzy_match_text(haystack, token) for token in search_tokens)):
                match_payload = compact_episode(dataset_path, episode, label, store, locks.get(episode_index, []))
                match_position = filtered_count
        if match_payload is not None and match_position is not None:
            return {
                "query": search_text,
                "match": match_payload,
                "page": (match_position - 1) // page_size + 1,
                "page_size": page_size,
                "position": match_position,
                "total": filtered_count,
            }
        return {
            "query": search_text,
            "match": None,
            "page": None,
            "page_size": page_size,
            "position": None,
            "total": filtered_count,
        }

    position = find_episode_position(dataset, search_text)
    if position is None:
        return {
            "query": search_text,
            "match": None,
            "page": None,
            "page_size": page_size,
            "position": None,
            "total": len(dataset["episodes"]),
        }

    episode = dataset["episodes"][position]
    episode_index = int(episode["episode_index"])
    label = label_for_episode(store, user, episode_index)
    locks = presence_snapshot(dataset_path, user)
    return {
        "query": search_text,
        "match": compact_episode(dataset_path, episode, label, store, locks.get(episode_index, [])),
        "page": position // page_size + 1,
        "page_size": page_size,
        "position": position + 1,
        "total": len(dataset["episodes"]),
    }


def filter_episodes(
    dataset_path: Path,
    dataset: dict[str, Any],
    store: dict[str, Any],
    user: str,
    query: dict[str, list[str]],
) -> list[dict[str, Any]]:
    search_text = (query_value(query, "q", "") or "").strip().lower()
    search_tokens = [token for token in re.split(r"\s+", search_text) if token]
    status_filter = query_value(query, "status", "all") or "all"
    if status_filter == "unlabeled":
        status_filter = "pending"
    result = []
    locks = presence_snapshot(dataset_path, user)
    for episode in dataset["episodes"]:
        episode_index = int(episode["episode_index"])
        label = label_for_episode(store, user, episode_index)
        status = review_status(label.get("status"))
        if status_filter != "all" and status != status_filter:
            continue
        haystack = episode_search_haystack(episode)
        if search_tokens and not all(fuzzy_match_text(haystack, token) for token in search_tokens):
            continue
        result.append(compact_episode(dataset_path, episode, label, store, locks.get(episode_index, [])))
    return result


def configured_dataset_contexts(
    settings: dict[str, Any] | None = None,
    refresh: bool = False,
) -> list[tuple[str, Path, dict[str, Any], dict[str, Any]]]:
    """Load every ready catalog entry for the unified review list."""
    contexts = []
    for raw_source in configured_dataset_sources(settings):
        source = canonical_dataset_source(raw_source)
        path = configured_dataset_path(source)
        if path is None:
            continue
        dataset = load_dataset(path, refresh=refresh)
        store = load_label_store(path)
        contexts.append((source, path, dataset, store))
    return contexts


def aggregate_dataset_counts(
    contexts: list[tuple[str, Path, dict[str, Any], dict[str, Any]]],
    user: str,
) -> dict[str, int]:
    counts = {"total": 0, "reject": 0, "pending": 0, "accept": 0, "marked": 0, "all_marked": 0}
    for _source, _path, dataset, store in contexts:
        current = status_counts(dataset, store, user)
        for key in counts:
            counts[key] += int(current.get(key) or 0)
    return counts


def aggregate_dataset_users(
    contexts: list[tuple[str, Path, dict[str, Any], dict[str, Any]]],
    user: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for _source, _path, dataset, store in contexts:
        for item in user_summaries(dataset, store):
            name = str(item.get("user") or "")
            if not name:
                continue
            target = merged.setdefault(
                name,
                {"user": name, "counts": {"total": 0, "reject": 0, "pending": 0, "accept": 0, "marked": 0, "all_marked": 0}},
            )
            for key, value in (item.get("counts") or {}).items():
                target["counts"][key] = int(target["counts"].get(key) or 0) + int(value or 0)
    return sorted(merged.values(), key=lambda item: str(item.get("user") or ""))


def label_rows_from_store(
    dataset_path: Path,
    dataset: dict[str, Any],
    store: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    episode_by_index = dataset.get("episode_by_index") or {}
    labels = store.get("labels") or {}
    for key in sorted(labels, key=lambda item: int(item) if str(item).isdigit() else str(item)):
        label = labels[key]
        if not isinstance(label, dict):
            continue
        try:
            episode_index = int(key)
        except ValueError:
            continue
        episode = episode_by_index.get(episode_index, {})
        user = label.get("user") or label.get("annotator") or "default"
        row = {
            "dataset_id": dataset_id(dataset_path),
            "dataset_path": str(dataset_path),
            "user": user,
            "annotator": label.get("annotator") or user,
            "episode_index": episode_index,
            "episode_name": f"episode_{episode_index:06d}",
            "episode_uuid": episode.get("episode_uuid", label.get("episode_uuid", "")),
            "status": review_status(label.get("status")),
            "issues": label.get("issues", []),
            "note": label.get("note", ""),
            "updated_at": label.get("updated_at", ""),
            "length": episode.get("length"),
            "task_description": episode.get("task_description", ""),
            "task_annotation": episode.get("task_annotation", ""),
        }
        rows.append(row)
    return rows


class QCRequestHandler(BaseHTTPRequestHandler):
    server_version = "LQCP/0.2"

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if getattr(self, "path", "").startswith("/media"):
            return
        super().log_request(code, size)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), format % args))

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_HEAD(self) -> None:
        self.handle_request("HEAD")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def handle_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            if not self.is_authorized(parsed):
                self.send_json({"error": "Unauthorized"}, status=401)
                return
            if parsed.path.startswith("/api/"):
                self.handle_api(method, parsed)
            elif parsed.path == "/media":
                self.handle_media(method, parsed)
            elif parsed.path == "/proxy_media":
                self.handle_proxy_media(method, parsed)
            elif parsed.path in {"/admin", "/admin/"}:
                self.handle_static(method, parsed, default_file="admin.html")
            elif parsed.path in {"/admin/review", "/admin/review/"}:
                self.handle_static(method, parsed, default_file="admin_review.html")
            elif parsed.path in {"/rank", "/rank/"}:
                self.handle_static(method, parsed, default_file="rank.html")
            elif parsed.path in {"/phone", "/phone/"}:
                self.handle_static(method, parsed, default_file="phone.html")
            else:
                self.handle_static(method, parsed)
        except AppError as exc:
            self.send_json({"error": exc.message}, status=exc.status)
        except BrokenPipeError:
            return
        except Exception as exc:
            self.log_message("Unhandled error: %r", exc)
            self.send_json({"error": str(exc)}, status=500)

    def is_authorized(self, parsed: Any) -> bool:
        token = SERVER_CONFIG.get("token") or ""
        if not token:
            return True
        path = parsed.path
        if not path.startswith("/api/") and path not in {"/media", "/proxy_media"}:
            return True
        query = parse_qs(parsed.query)
        supplied = query_value(query, "token") or self.headers.get("X-LQCP-Token") or ""
        return supplied == token

    def ensure_client_cookie(self) -> tuple[str, str]:
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        client_id = cookie.get(USER_SESSION_COOKIE).value if cookie.get(USER_SESSION_COOKIE) else ""
        if not is_valid_client_id(client_id):
            client_id = generate_client_id()
        morsel = SimpleCookie()
        morsel[USER_SESSION_COOKIE] = client_id
        morsel[USER_SESSION_COOKIE]["path"] = "/"
        morsel[USER_SESSION_COOKIE]["max-age"] = str(USER_SESSION_TTL_SECONDS)
        morsel[USER_SESSION_COOKIE]["samesite"] = "Lax"
        morsel[USER_SESSION_COOKIE]["httponly"] = True
        return client_id, morsel.output(header="").strip()

    def read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError(f"Invalid JSON body: {exc}", 400)
        if not isinstance(data, dict):
            raise AppError("JSON body must be an object", 400)
        return data

    def send_json(self, payload: Any, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def handle_api(self, method: str, parsed: Any) -> None:
        query = parse_qs(parsed.query)
        user = normalize_user(query_value(query, "user"))

        if parsed.path == "/api/session":
            client_id, cookie_header = self.ensure_client_cookie()
            if method == "GET":
                self.send_json(read_user_session(client_id), extra_headers={"Set-Cookie": cookie_header})
                return
            if method == "POST":
                payload = self.read_body_json()
                session_user = normalize_user(payload.get("user"))
                self.send_json(save_user_session(client_id, session_user), extra_headers={"Set-Cookie": cookie_header})
                return
            raise AppError("Method not allowed", 405)

        if parsed.path == "/api/settings":
            if method == "GET":
                dataset_path = safe_dataset_path(None)
                dataset = load_dataset(dataset_path)
                self.send_json(current_dataset_payload(dataset_path, dataset))
                return
            if method == "POST":
                payload = self.read_body_json()
                dataset_paths = payload.get("dataset_paths")
                if not isinstance(dataset_paths, list):
                    dataset_paths = payload.get("dataset_path")
                active_dataset_source = (
                    payload.get("active_dataset_source")
                    or payload.get("active_dataset")
                    or ""
                )
                settings = save_configured_datasets(
                    dataset_paths,
                    user,
                    refresh_remote=bool(payload.get("refresh_remote")),
                    active_dataset_source=active_dataset_source,
                )
                self.send_json({"ok": True, **settings})
                return
            raise AppError("Method not allowed", 405)

        dataset_path = safe_dataset_path(query_value(query, "dataset") or query_value(query, "dataset_path"))
        refresh = query_value(query, "refresh") == "1"
        dataset = load_dataset(dataset_path, refresh=refresh)
        store = load_label_store(dataset_path)

        if method == "GET" and parsed.path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "dataset_path": str(dataset_path),
                    "dataset_source": dataset_source_for_path(dataset_path),
                    "dataset_id": dataset_id(dataset_path),
                    "remote_dataset": remote_dataset_manifest(dataset_path),
                    "proxy_build": proxy_build_status(dataset_path),
                    "raw_episode_roots": [str(root) for root in configured_raw_episode_roots()],
                    "source_metadata": {
                        "dm3_enabled": dm3_enabled(),
                        "dm3_base_url": DM3_BASE_URL if dm3_enabled() else "",
                        "raw_metadata_fallback": raw_metadata_fallback_enabled(),
                    },
                    "user": user,
                    "time": utc_now(),
                }
            )
            return

        if method == "GET" and parsed.path == "/api/proxy_status":
            self.send_json(proxy_build_status(dataset_path))
            return

        if method == "GET" and parsed.path == "/api/proxy_history":
            limit = parse_int(query_value(query, "limit"), PROXY_BUILD_HISTORY_LIMIT, 1, 5000)
            include_all = query_value(query, "all") == "1"
            self.send_json(proxy_build_history_payload(None if include_all else dataset_path, limit))
            return

        if method == "POST" and parsed.path == "/api/proxy_build":
            payload = self.read_body_json()
            action = str(payload.get("action") or "start").strip().lower()
            if action == "cancel":
                self.send_json({"ok": True, **cancel_proxy_build(dataset_path)})
                return
            episode_value = payload.get("episode_index")
            if episode_value is not None:
                episode_index = int(episode_value)
                if episode_index not in dataset["episode_by_index"]:
                    raise AppError(f"Episode not found: {episode_index}", 404)
                self.send_json({"ok": True, **prioritize_proxy_episode(dataset_path, dataset, episode_index)})
                return
            force = bool(payload.get("force"))
            self.send_json({"ok": True, **schedule_proxy_build(dataset_path, dataset, reason=action or "manual", force=force)})
            return

        if method == "GET" and parsed.path == "/api/users":
            self.send_json({"users": user_summaries(dataset, store)})
            return

        if method == "GET" and parsed.path == "/api/summary":
            self.send_json(
                {
                    "dataset_path": str(dataset_path),
                    "dataset_source": dataset_source_for_path(dataset_path),
                    "dataset_id": dataset_id(dataset_path),
                    "user": user,
                    "info": dataset["info"],
                    "tasks": dataset["tasks"],
                    "counts": status_counts(dataset, store, user),
                    "users": user_summaries(dataset, store),
                    "labels_path": str(labels_path(dataset_path)),
                    "labels_jsonl_path": str(labels_jsonl_path(dataset_path)),
                    "labels_db_path": str(labels_db_path(dataset_path)),
                }
            )
            return

        if method == "GET" and parsed.path == "/api/admin":
            self.send_json(admin_payload(dataset_path, dataset, store))
            return

        if method == "GET" and parsed.path == "/api/rank":
            self.send_json(rank_payload(dataset_path, dataset, store))
            return

        if method == "GET" and parsed.path == "/api/episode_lookup":
            if query_value(query, "all_datasets", "") == "1":
                contexts = configured_dataset_contexts(load_server_settings())
                matches = []
                for source, path, item_dataset, item_store in contexts:
                    matches.extend(
                        {
                            **row,
                            "dataset_id": dataset_id(path),
                            "dataset_source": source,
                            "dataset_name": dataset_source_name(source, path),
                        }
                        for row in filter_episodes(path, item_dataset, item_store, user, query)
                    )
                matches.sort(key=lambda row: (str(row.get("dataset_name") or ""), int(row.get("episode_index") or 0)))
                match = matches[0] if matches else None
                return_payload = {
                    "query": query_value(query, "q", "") or "",
                    "match": match,
                    "page": ((0 // parse_int(query_value(query, "page_size"), 60, 1, 200)) + 1) if match else None,
                    "page_size": parse_int(query_value(query, "page_size"), 60, 1, 200),
                    "position": 1 if match else None,
                    "total": len(matches),
                }
                self.send_json(return_payload)
                return
            self.send_json(episode_lookup_payload(dataset_path, dataset, store, user, query))
            return

        if method == "GET" and parsed.path == "/api/episodes":
            page = parse_int(query_value(query, "page"), 1, 1, 100000)
            page_size = parse_int(query_value(query, "page_size"), 60, 1, 200)
            if query_value(query, "all_datasets", "") == "1":
                contexts = configured_dataset_contexts(load_server_settings(), refresh=refresh)
                merged: list[dict[str, Any]] = []
                for source, path, item_dataset, item_store in contexts:
                    rows = filter_episodes(path, item_dataset, item_store, user, query)
                    for row in rows:
                        row["dataset_id"] = dataset_id(path)
                        row["dataset_source"] = source
                        row["dataset_name"] = dataset_source_name(source, path)
                    merged.extend(rows)
                merged.sort(key=lambda row: (str(row.get("dataset_name") or ""), int(row.get("episode_index") or 0)))
                page_count = max(1, math.ceil(len(merged) / page_size))
                page = min(page, page_count)
                start = (page - 1) * page_size
                current_info = {
                    "total_episodes": sum(len(item_dataset["episodes"]) for _source, _path, item_dataset, _store in contexts),
                    "total_frames": sum(int(item_dataset["info"].get("total_frames") or 0) for _source, _path, item_dataset, _store in contexts),
                    "fps": next((item_dataset["info"].get("fps") for _source, _path, item_dataset, _store in contexts if item_dataset["info"].get("fps") is not None), None),
                    "robot_type": "multiple" if len(contexts) > 1 else (contexts[0][2]["info"].get("robot_type") if contexts else None),
                }
                self.send_json(
                    {
                        "all_datasets": True,
                        "dataset_path": str(dataset_path),
                        "dataset_source": dataset_source_for_path(dataset_path),
                        "dataset_id": "multi-dataset",
                        "active_dataset_id": load_server_settings().get("active_dataset_id"),
                        "datasets": dataset_catalog(load_server_settings()),
                        "user": user,
                        "page": page,
                        "page_count": page_count,
                        "page_size": page_size,
                        "total": len(merged),
                        "episodes": merged[start : start + page_size],
                        "counts": aggregate_dataset_counts(contexts, user),
                        "users": aggregate_dataset_users(contexts, user),
                        "info": current_info,
                    }
                )
                return
            filtered = filter_episodes(dataset_path, dataset, store, user, query)
            page_count = max(1, math.ceil(len(filtered) / page_size))
            page = min(page, page_count)
            start = (page - 1) * page_size
            end = start + page_size
            self.send_json(
                {
                    "dataset_path": str(dataset_path),
                    "dataset_source": dataset_source_for_path(dataset_path),
                    "dataset_id": dataset_id(dataset_path),
                    "user": user,
                    "page": page,
                    "page_count": page_count,
                    "page_size": page_size,
                    "total": len(filtered),
                    "episodes": filtered[start:end],
                    "counts": status_counts(dataset, store, user),
                    "users": user_summaries(dataset, store),
                    "info": {
                        "total_episodes": dataset["info"].get("total_episodes", len(dataset["episodes"])),
                        "total_frames": dataset["info"].get("total_frames"),
                        "fps": dataset["info"].get("fps"),
                        "robot_type": dataset["info"].get("robot_type"),
                    },
                }
            )
            return

        if method == "GET" and parsed.path == "/api/episode":
            episode_index = parse_int(query_value(query, "episode_index"), 0, 0, 10000000)
            heartbeat_episode(dataset_path, user, episode_index)
            self.send_json(full_episode(dataset_path, dataset, episode_index, store, user))
            return

        if method == "GET" and parsed.path == "/api/admin/episode":
            episode_index = parse_int(query_value(query, "episode_index"), 0, 0, 10000000)
            self.send_json(full_episode(dataset_path, dataset, episode_index, store, user))
            return

        if method == "GET" and parsed.path == "/api/source_metadata":
            episode_uuid = (query_value(query, "episode_uuid", "") or "").strip()
            episode_index_value = query_value(query, "episode_index")
            episode: dict[str, Any] | None = None
            if episode_uuid:
                for candidate in dataset["episodes"]:
                    if str(candidate.get("episode_uuid", "")).lower() == episode_uuid.lower():
                        episode = candidate
                        break
            elif episode_index_value is not None:
                episode_index = parse_int(episode_index_value, 0, 0, 10000000)
                episode = dataset["episode_by_index"].get(episode_index)
            if episode is not None:
                episode_uuid = str(episode.get("episode_uuid", "") or episode_uuid)
            if not episode_uuid:
                raise AppError("episode_index or episode_uuid is required", 400)
            self.send_json(
                {
                    "episode_index": episode.get("episode_index") if episode else None,
                    "episode_uuid": episode_uuid,
                    "source_metadata": source_metadata_for_episode(dataset_path, episode, episode_uuid),
                }
            )
            return

        if method == "GET" and parsed.path == "/api/episode_state":
            episode_index = parse_int(query_value(query, "episode_index"), 0, 0, 10000000)
            episode = dataset["episode_by_index"].get(episode_index)
            if episode is None:
                raise AppError(f"Episode not found: {episode_index}", 404)
            heartbeat_episode(dataset_path, user, episode_index)
            label = label_for_episode(store, user, episode_index)
            locked_by = presence_snapshot(dataset_path, user).get(episode_index, [])
            self.send_json(
                {
                    "episode_index": episode_index,
                    "label": label,
                    "summary": compact_episode(dataset_path, episode, label, store, locked_by),
                    "episode_label_summary": per_episode_label_summary(store, episode_index),
                    "active_users": locked_by,
                    "counts": status_counts(dataset, store, user),
                    "users": user_summaries(dataset, store),
                }
            )
            return

        if method == "POST" and parsed.path == "/api/presence":
            payload = self.read_body_json()
            episode_index = int(payload.get("episode_index"))
            if episode_index not in dataset["episode_by_index"]:
                raise AppError(f"Episode not found: {episode_index}", 404)
            action = str(payload.get("action") or "heartbeat")
            if action == "release":
                release_episode_presence(dataset_path, user, episode_index)
            else:
                heartbeat_episode(dataset_path, user, episode_index)
            locked_by = presence_snapshot(dataset_path, user).get(episode_index, [])
            self.send_json({"ok": True, "episode_index": episode_index, "active_users": locked_by})
            return

        if method == "GET" and parsed.path == "/api/trajectory":
            episode_index = parse_int(query_value(query, "episode_index"), 0, 0, 10000000)
            max_points = parse_int(query_value(query, "max_points"), 900, 100, 2000)
            self.send_json(load_trajectory(dataset_path, dataset, episode_index, max_points=max_points))
            return

        if method == "GET" and parsed.path == "/api/export.jsonl":
            rows = label_rows_from_store(dataset_path, dataset, store)
            body = "".join(json.dumps(row, ensure_ascii=False, default=json_default) + "\n" for row in rows)
            filename = f"{dataset_id(dataset_path)}-labels.jsonl"
            self.send_bytes(
                body.encode("utf-8"),
                "application/x-ndjson; charset=utf-8",
                extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return

        if method == "GET" and parsed.path == "/api/export.csv":
            rows = label_rows_from_store(dataset_path, dataset, store)
            output = io.StringIO()
            fieldnames = [
                "dataset_id",
                "dataset_path",
                "user",
                "annotator",
                "episode_index",
                "episode_name",
                "episode_uuid",
                "status",
                "issues",
                "note",
                "updated_at",
                "length",
                "task_description",
                "task_annotation",
            ]
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                csv_row = dict(row)
                csv_row["issues"] = "|".join(row.get("issues") or [])
                writer.writerow(csv_row)
            filename = f"{dataset_id(dataset_path)}-labels.csv"
            self.send_bytes(
                ("\ufeff" + output.getvalue()).encode("utf-8"),
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return

        if method == "POST" and parsed.path == "/api/label":
            payload = self.read_body_json()
            episode_index = int(payload.get("episode_index"))
            if episode_index not in dataset["episode_by_index"]:
                raise AppError(f"Episode not found: {episode_index}", 404)
            status = str(payload.get("status", "unlabeled"))
            if status not in STATUS_VALUES:
                raise AppError(f"Invalid status: {status}", 400)

            issues = payload.get("issues") or []
            if not isinstance(issues, list):
                raise AppError("issues must be a list", 400)
            issues = [str(item)[:80] for item in issues if str(item).strip()]
            note = str(payload.get("note") or "")[:2000]

            with LABEL_LOCK:
                updated_store = write_label_db(dataset_path, dataset, user, episode_index, status, issues, note)
            client_id, cookie_header = self.ensure_client_cookie()
            save_user_session(client_id, user)

            heartbeat_episode(dataset_path, user, episode_index)
            locked_by = presence_snapshot(dataset_path, user).get(episode_index, [])
            label = label_for_episode(updated_store, user, episode_index)
            self.send_json(
                {
                    "ok": True,
                    "user": user,
                    "label": label,
                    "episode_label_summary": per_episode_label_summary(updated_store, episode_index),
                    "summary": compact_episode(dataset_path, dataset["episode_by_index"][episode_index], label, updated_store, locked_by),
                    "counts": status_counts(dataset, updated_store, user),
                    "users": user_summaries(dataset, updated_store),
                    "labels_path": str(labels_path(dataset_path)),
                    "labels_jsonl_path": str(labels_jsonl_path(dataset_path)),
                    "labels_db_path": str(labels_db_path(dataset_path)),
                },
                extra_headers={"Set-Cookie": cookie_header},
            )
            return

        if method == "POST" and parsed.path == "/api/admin/label":
            payload = self.read_body_json()
            episode_index = int(payload.get("episode_index"))
            if episode_index not in dataset["episode_by_index"]:
                raise AppError(f"Episode not found: {episode_index}", 404)
            status = str(payload.get("status", "pending"))
            if status not in RECORDED_STATUS_VALUES:
                raise AppError(f"Invalid status: {status}", 400)

            with LABEL_LOCK:
                updated_store = write_label_db(dataset_path, dataset, user, episode_index, status, [], "", force=True)
            client_id, cookie_header = self.ensure_client_cookie()
            save_user_session(client_id, user)

            label = label_for_episode(updated_store, user, episode_index)
            locked_by = presence_snapshot(dataset_path, user).get(episode_index, [])
            self.send_json(
                {
                    "ok": True,
                    "user": user,
                    "label": label,
                    "episode_label_summary": per_episode_label_summary(updated_store, episode_index),
                    "summary": compact_episode(dataset_path, dataset["episode_by_index"][episode_index], label, updated_store, locked_by),
                    "counts": status_counts_from_label_map(dataset, updated_store.get("labels") or {}),
                    "users": user_summaries(dataset, updated_store),
                    "admin": admin_payload(dataset_path, dataset, updated_store),
                    "labels_path": str(labels_path(dataset_path)),
                    "labels_jsonl_path": str(labels_jsonl_path(dataset_path)),
                    "labels_db_path": str(labels_db_path(dataset_path)),
                },
                extra_headers={"Set-Cookie": cookie_header},
            )
            return

        raise AppError("Not found", 404)

    def handle_media(self, method: str, parsed: Any) -> None:
        if method not in {"GET", "HEAD"}:
            raise AppError("Method not allowed", 405)
        query = parse_qs(parsed.query)
        dataset_path = safe_dataset_path(query_value(query, "dataset") or query_value(query, "dataset_path"))
        rel_path = safe_rel_media_path(query_value(query, "rel"))
        file_path = (dataset_path / Path(*rel_path.parts)).resolve()
        if str(file_path) != str(dataset_path) and not str(file_path).startswith(str(dataset_path) + os.sep):
            raise AppError("Invalid media path", 403)
        if not file_path.is_file():
            raise AppError("Media file not found", 404)
        self.send_file(file_path, cache_control="public, max-age=3600")

    def handle_proxy_media(self, method: str, parsed: Any) -> None:
        if method not in {"GET", "HEAD"}:
            raise AppError("Method not allowed", 405)
        query = parse_qs(parsed.query)
        dataset_path = safe_dataset_path(query_value(query, "dataset") or query_value(query, "dataset_path"))
        file_path = proxy_video_path(dataset_path, query_value(query, "rel"))
        if not file_path.is_file():
            raise AppError("Proxy media file not found", 404)
        self.send_file(file_path, cache_control="public, max-age=86400")

    def handle_static(self, method: str, parsed: Any, default_file: str = "index.html") -> None:
        if method not in {"GET", "HEAD"}:
            raise AppError("Method not allowed", 405)
        path = unquote(parsed.path)
        if path in {"", "/"}:
            rel = default_file
        elif path in {"/admin", "/admin/"}:
            rel = default_file
        elif path in {"/admin/review", "/admin/review/"}:
            rel = default_file
        elif path in {"/rank", "/rank/"}:
            rel = default_file
        elif path in {"/phone", "/phone/"}:
            rel = default_file
        else:
            rel = posixpath.normpath(path.lstrip("/"))
        if rel.startswith("../") or rel == "..":
            raise AppError("Invalid static path", 400)
        file_path = (STATIC_ROOT / rel).resolve()
        if str(file_path) != str(STATIC_ROOT) and not str(file_path).startswith(str(STATIC_ROOT) + os.sep):
            raise AppError("Invalid static path", 403)
        if not file_path.is_file():
            raise AppError("Not found", 404)
        self.send_file(file_path, cache_control="no-store")

    def send_file(self, file_path: Path, cache_control: str = "public, max-age=3600") -> None:
        file_size = file_path.stat().st_size
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        range_header = self.headers.get("Range")

        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if not match:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            start_raw, end_raw = match.groups()
            if start_raw == "" and end_raw == "":
                start, end = 0, file_size - 1
            elif start_raw == "":
                suffix_length = int(end_raw)
                start = max(0, file_size - suffix_length)
                end = file_size - 1
            else:
                start = int(start_raw)
                end = int(end_raw) if end_raw else file_size - 1
            if start >= file_size or end < start:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return
            end = min(end, file_size - 1)
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            if self.command == "HEAD":
                return
            self.copy_file_bytes(file_path, start, length)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if self.command == "HEAD":
            return
        self.copy_file_bytes(file_path, 0, file_size)

    def copy_file_bytes(self, file_path: Path, start: int, length: int) -> None:
        with file_path.open("rb") as handle:
            sendfile = getattr(os, "sendfile", None)
            if sendfile:
                offset = start
                remaining = length
                out_fd = self.connection.fileno()
                in_fd = handle.fileno()
                while remaining > 0:
                    sent = sendfile(out_fd, in_fd, offset, min(8 * 1024 * 1024, remaining))
                    if sent == 0:
                        break
                    offset += sent
                    remaining -= sent
                return
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LeRobot manual quality-check platform")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "18080")))
    parser.add_argument("--dataset", default=os.environ.get("DATASET_PATH", DEFAULT_DATASET))
    parser.add_argument("--token", default=os.environ.get("LQCP_TOKEN", ""))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    QC_ROOT.mkdir(parents=True, exist_ok=True)
    SERVER_CONFIG.update(
        {
            "default_dataset": args.dataset,
            "token": args.token,
        }
    )

    try:
        dataset_path = safe_dataset_path(None)
        dataset = load_dataset(dataset_path, refresh=True)
        print(
            f"Loaded {len(dataset['episodes'])} episodes and "
            f"{sum(len(v) for v in dataset['videos_by_episode'].values())} videos from {dataset_path}",
            flush=True,
        )
    except Exception as exc:
        print(f"Warning: default dataset could not be loaded: {exc}", file=sys.stderr, flush=True)

    address = (args.host, args.port)
    httpd = QCThreadingHTTPServer(address, QCRequestHandler)
    print(f"LerobotQualityCheckPlatform listening on http://{args.host}:{args.port}", flush=True)
    if args.token:
        print("Token authentication is enabled. Add ?token=<token> to the URL.", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
