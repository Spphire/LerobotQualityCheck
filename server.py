#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
from urllib.parse import parse_qs, unquote, urlencode, urlparse


DEFAULT_DATASET = "/mnt/nm_dataset/dataset/giftbox_0628_1912episodes"
PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PROJECT_ROOT / "web"
QC_ROOT = PROJECT_ROOT / "qc_results"
VIDEO_PROXY_ROOT = PROJECT_ROOT / "video_proxy"
SETTINGS_PATH = QC_ROOT / "settings.json"
USER_SESSIONS_DB_PATH = QC_ROOT / "user_sessions.db"
ALLOWED_DATASET_ROOT = Path("/mnt").resolve()
RAW_METADATA_TIMEOUT_SECONDS = float(os.environ.get("LQCP_RAW_METADATA_TIMEOUT", "3"))
COLLECTOR_CACHE_WORKERS = max(1, int(os.environ.get("LQCP_COLLECTOR_CACHE_WORKERS", "3")))
COLLECTOR_CACHE_NEGATIVE_TTL_SECONDS = int(os.environ.get("LQCP_COLLECTOR_CACHE_NEGATIVE_TTL", str(24 * 60 * 60)))

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
COLLECTOR_CACHE_LOCK = threading.Lock()
LABEL_LOCK = threading.Lock()
PRESENCE_LOCK = threading.Lock()
SETTINGS_LOCK = threading.Lock()
USER_SESSION_LOCK = threading.Lock()
SERVER_CONFIG: dict[str, Any] = {}


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


def load_server_settings() -> dict[str, Any]:
    payload = read_json(SETTINGS_PATH, fallback={})
    return payload if isinstance(payload, dict) else {}


def current_dataset_raw_path() -> str:
    settings = load_server_settings()
    dataset_path = str(settings.get("dataset_path") or "").strip()
    if dataset_path:
        return dataset_path
    return str(SERVER_CONFIG.get("default_dataset") or DEFAULT_DATASET)


def safe_dataset_path(raw_path: str | None) -> Path:
    raw_path = raw_path or current_dataset_raw_path()
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


def current_dataset_payload(dataset_path: Path, dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_server_settings()
    info = (dataset or {}).get("info") or {}
    episodes = (dataset or {}).get("episodes") or []
    return {
        "dataset_path": str(dataset_path),
        "dataset_id": dataset_id(dataset_path),
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


def save_current_dataset(raw_path: str | None, user: str) -> dict[str, Any]:
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise AppError("dataset_path is required", 400)
    dataset_path = safe_dataset_path(raw_path)
    require_ready_dataset(dataset_path)
    dataset = load_dataset(dataset_path, refresh=True)
    payload = {
        **current_dataset_payload(dataset_path, dataset),
        "updated_at": utc_now(),
        "updated_by": user,
    }
    with SETTINGS_LOCK:
        write_json_atomic(SETTINGS_PATH, payload)
    return payload


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
    if "raw_roots" not in columns:
        conn.execute("ALTER TABLE collector_cache ADD COLUMN raw_roots TEXT NOT NULL DEFAULT ''")
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


def safe_rel_media_path(raw_rel: str | None) -> PurePosixPath:
    if not raw_rel:
        raise AppError("Missing media path", 400)
    rel_path = PurePosixPath(unquote(raw_rel))
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise AppError("Invalid media path", 400)
    return rel_path


def proxy_video_path(dataset_path: Path, rel_path: str) -> Path:
    rel = safe_rel_media_path(rel_path)
    proxy_root = (VIDEO_PROXY_ROOT / dataset_id(dataset_path)).resolve()
    file_path = (proxy_root / Path(*rel.parts)).resolve()
    if str(file_path) != str(proxy_root) and not str(file_path).startswith(str(proxy_root) + os.sep):
        raise AppError("Invalid proxy media path", 403)
    return file_path


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
        for key in ("name", "username", "user", "id", "uid"):
            text = string_value(value.get(key))
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
        ("采集人",),
        ("采集员",),
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
    return ""


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
        "raw_path": "",
        "metadata_path": "",
        "raw_roots": raw_episode_roots_signature(),
        "found": False,
    }
    for metadata_path in raw_metadata_candidate_paths(uuid):
        metadata = read_raw_metadata_with_timeout(metadata_path)
        if metadata is None:
            continue
        result = {
            "episode_uuid": uuid,
            "collector": collector_from_metadata(metadata),
            "raw_path": str(metadata_path.parents[1]),
            "metadata_path": str(metadata_path),
            "raw_roots": raw_episode_roots_signature(),
            "found": True,
        }
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
        "raw_path": "",
        "metadata_path": "",
        "raw_roots": raw_episode_roots_signature(),
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
    if payload.get("raw_roots") != raw_episode_roots_signature():
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
                   collector, raw_path, metadata_path, raw_roots, found, status, attempts,
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
                   collector, raw_path, metadata_path, raw_roots, found, status, attempts,
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
                collector, raw_path, metadata_path, raw_roots, found, status, attempts,
                last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id, episode_index) DO UPDATE SET
                dataset_path = excluded.dataset_path,
                episode_name = excluded.episode_name,
                episode_uuid = excluded.episode_uuid,
                collector = excluded.collector,
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
                str(source_metadata.get("raw_path") or ""),
                str(source_metadata.get("metadata_path") or ""),
                str(source_metadata.get("raw_roots") or raw_episode_roots_signature()),
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
        "raw_path": str(source_metadata.get("raw_path") or ""),
        "metadata_path": str(source_metadata.get("metadata_path") or ""),
        "raw_roots": str(source_metadata.get("raw_roots") or raw_episode_roots_signature()),
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
    try:
        source_metadata = raw_episode_metadata(episode_uuid)
        if not source_metadata:
            source_metadata = empty_collector_metadata(episode, status="missing")
        return upsert_collector_cache(dataset_path, episode, source_metadata)
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
    prefetch_key = (dataset_id(dataset_path), fingerprint, raw_episode_roots_signature())
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
        return raw_episode_metadata(episode_uuid)
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
            collector = "未知采集人"
        stat = collector_stats.setdefault(
            collector,
            {
                "collector": collector,
                "marked": 0,
                "reject": 0,
                "accept": 0,
                "pending": 0,
                "known": collector != "未知采集人",
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
    return {
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
    videos = []
    for video in dataset["videos_by_episode"].get(episode_index, []):
        item = dict(video)
        item["url"] = media_url(dataset_path, video["rel_path"])
        videos.append(item)
    label = label_for_episode(store, user, episode_index)
    locked_by = presence_snapshot(dataset_path, user).get(episode_index, [])
    source_metadata = cached_or_queued_source_metadata(dataset_path, episode, priority=10)
    return {
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
    is_teleop = "teleoperation" in device_type.lower() or "teleoperation" in collection_mode.lower()

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
                settings = save_current_dataset(payload.get("dataset_path"), user)
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
                    "dataset_id": dataset_id(dataset_path),
                    "raw_episode_roots": [str(root) for root in configured_raw_episode_roots()],
                    "user": user,
                    "time": utc_now(),
                }
            )
            return

        if method == "GET" and parsed.path == "/api/users":
            self.send_json({"users": user_summaries(dataset, store)})
            return

        if method == "GET" and parsed.path == "/api/summary":
            self.send_json(
                {
                    "dataset_path": str(dataset_path),
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
            self.send_json(episode_lookup_payload(dataset_path, dataset, store, user, query))
            return

        if method == "GET" and parsed.path == "/api/episodes":
            page = parse_int(query_value(query, "page"), 1, 1, 100000)
            page_size = parse_int(query_value(query, "page_size"), 60, 1, 200)
            filtered = filter_episodes(dataset_path, dataset, store, user, query)
            start = (page - 1) * page_size
            end = start + page_size
            self.send_json(
                {
                    "dataset_path": str(dataset_path),
                    "dataset_id": dataset_id(dataset_path),
                    "user": user,
                    "page": page,
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
    httpd = ThreadingHTTPServer(address, QCRequestHandler)
    print(f"LerobotQualityCheckPlatform listening on http://{args.host}:{args.port}", flush=True)
    if args.token:
        print("Token authentication is enabled. Add ?token=<token> to the URL.", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
