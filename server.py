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
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
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
ALLOWED_DATASET_ROOT = Path("/mnt").resolve()

STATUS_VALUES = {"reject", "pending", "accept", "unlabeled"}
STATUS_ALIASES = {"bad": "reject", "review": "pending", "good": "accept"}
RECORDED_STATUS_VALUES = {"reject", "pending", "accept"}
DECISION_STATUS_VALUES = {"reject", "accept"}
EPISODE_RE = re.compile(r"episode_(\d+)\.(mp4|parquet)$")
PRESENCE_TTL_SECONDS = 8.0

DATASET_CACHE: dict[str, dict[str, Any]] = {}
TRAJECTORY_CACHE: dict[tuple[str, int, int, int], dict[str, Any]] = {}
EPISODE_PRESENCE: dict[str, dict[str, dict[str, float]]] = {}
DATASET_CACHE_LOCK = threading.Lock()
LABEL_LOCK = threading.Lock()
PRESENCE_LOCK = threading.Lock()
SERVER_CONFIG: dict[str, Any] = {}


class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


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


def safe_dataset_path(raw_path: str | None) -> Path:
    raw_path = raw_path or SERVER_CONFIG.get("default_dataset") or DEFAULT_DATASET
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
            if existing and existing.get("user") != user and review_status(existing.get("status")) != review_status(status):
                conn.rollback()
                raise AppError(
                    f"Episode {episode_index} already labeled {existing.get('status')} by {existing.get('user')}",
                    409,
                )
            if existing and existing.get("user") != user and review_status(existing.get("status")) == review_status(status):
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
    label = (store.get("labels") or {}).get(str(episode_index))
    if label:
        status = review_status(label.get("status"))
        if status in RECORDED_STATUS_VALUES:
            statuses[status] += 1
            users.append(str(label.get("user") or label.get("annotator") or "default"))
    return {
        "label_count": sum(statuses.values()),
        "statuses": statuses,
        "users": users,
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
    return dataset


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
    return {
        "episode": episode,
        "summary": compact_episode(dataset_path, episode, label, store, locked_by),
        "videos": videos,
        "label": label,
        "active_users": locked_by,
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
    left_gripper: list[float | None] = []
    right_gripper: list[float | None] = []
    masks = {"left": [], "right": [], "ego": []}
    ranges: dict[str, list[float | None]] = {"x": [None, None], "y": [None, None], "z": [None, None]}

    for row_index, row in enumerate(rows[::stride]):
        state = row.get("observation.state")
        frame = row.get("frame_index")
        frames.append(int(frame) if frame is not None else row_index * stride)
        timestamps.append(clean_float(row.get("timestamp")))

        left = point_from_state(state, 0)
        right = point_from_state(state, 8)
        ego = point_from_state(state, 16)
        left_quat = quat_from_state(state, 3)
        right_quat = quat_from_state(state, 11)
        ego_quat = quat_from_state(state, 19)
        if left[0] is None:
            left = point_from_pose(row.get("observation.extra.left.raw_pose"))
            left_quat = quat_from_pose(row.get("observation.extra.left.raw_pose"))
        if right[0] is None:
            right = point_from_pose(row.get("observation.extra.right.raw_pose"))
            right_quat = quat_from_pose(row.get("observation.extra.right.raw_pose"))
        if ego[0] is None:
            ego = point_from_pose(row.get("observation.extra.ego.raw_pose"))
            ego_quat = quat_from_pose(row.get("observation.extra.ego.raw_pose"))

        left_points.append(left)
        right_points.append(right)
        ego_points.append(ego)
        left_quats.append(left_quat)
        right_quats.append(right_quat)
        ego_quats.append(ego_quat)
        update_ranges(ranges, left)
        update_ranges(ranges, right)
        update_ranges(ranges, ego)

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
        "frames": frames,
        "timestamps": timestamps,
        "left": {"points": left_points, "quaternions": left_quats, "gripper": left_gripper},
        "right": {"points": right_points, "quaternions": right_quats, "gripper": right_gripper},
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

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
        dataset_path = safe_dataset_path(query_value(query, "dataset") or query_value(query, "dataset_path"))
        user = normalize_user(query_value(query, "user"))
        refresh = query_value(query, "refresh") == "1"
        dataset = load_dataset(dataset_path, refresh=refresh)
        store = load_label_store(dataset_path)

        if method == "GET" and parsed.path == "/api/health":
            self.send_json(
                {
                    "ok": True,
                    "dataset_path": str(dataset_path),
                    "dataset_id": dataset_id(dataset_path),
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
                }
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
        dataset_path = safe_dataset_path(args.dataset)
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
