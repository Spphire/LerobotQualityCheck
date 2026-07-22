#!/usr/bin/env python3
"""Read-only integrity checker for LeRobot v2/v2.1 datasets."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

EPISODE_FILE_RE = re.compile(r"episode_(\d{6})")
LATENT_FILE_RE = re.compile(r"episode_(\d{6})_(\d+)_(\d+)\.pth$")
REQUIRED_META = ("info.json", "episodes.jsonl", "episodes_stats.jsonl", "tasks.jsonl")
CRITICAL_COLUMNS = ("episode_index", "frame_index", "index", "timestamp", "task_index")


def issue(report: dict[str, Any], severity: str, code: str, message: str, **details: Any) -> None:
    item = {"severity": severity, "code": code, "message": message}
    item.update({key: value for key, value in details.items() if value is not None})
    report["issues"].append(item)


def finish_report(report: dict[str, Any], started: float) -> dict[str, Any]:
    errors = sum(item["severity"] == "error" for item in report["issues"])
    warnings = sum(item["severity"] == "warning" for item in report["issues"])
    report["error_count"] = errors
    report["warning_count"] = warnings
    report["status"] = "PASS" if errors == 0 else "FAIL"
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            records.append(value)
    return records


def ids_from_paths(paths: list[Path]) -> tuple[dict[int, list[Path]], list[str]]:
    mapping: dict[int, list[Path]] = {}
    invalid: list[str] = []
    for path in paths:
        match = EPISODE_FILE_RE.search(path.name)
        if not match:
            invalid.append(str(path))
            continue
        mapping.setdefault(int(match.group(1)), []).append(path)
    return mapping, invalid


def compare_episode_set(
    report: dict[str, Any], label: str, observed: set[int], expected: set[int]
) -> None:
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if missing:
        issue(report, "error", f"missing_{label}", f"Missing {label} for {len(missing)} episodes", episodes=missing)
    if extra:
        issue(report, "error", f"extra_{label}", f"Unexpected {label} for {len(extra)} episodes", episodes=extra)


def check_indexed_records(
    report: dict[str, Any], label: str, records: list[dict[str, Any]], expected: set[int]
) -> dict[int, dict[str, Any]]:
    values: list[int] = []
    by_id: dict[int, dict[str, Any]] = {}
    for offset, record in enumerate(records):
        value = record.get("episode_index")
        if not isinstance(value, int):
            issue(report, "error", f"invalid_{label}_index", f"{label} record {offset} has no integer episode_index")
            continue
        values.append(value)
        by_id.setdefault(value, record)
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        issue(report, "error", f"duplicate_{label}_index", f"Duplicate episode indexes in {label}", episodes=duplicates)
    compare_episode_set(report, label, set(values), expected)
    if values and values != sorted(values):
        issue(report, "error", f"unordered_{label}", f"{label} records are not ordered by episode_index")
    return by_id


def format_dataset_path(pattern: str, episode_index: int, chunks_size: int, **values: Any) -> Path:
    episode_chunk = episode_index // chunks_size
    return Path(pattern.format(episode_index=episode_index, episode_chunk=episode_chunk, **values))


def parquet_check(task: tuple[str, int, int, float, list[str]]) -> dict[str, Any]:
    path_text, episode_index, expected_length, fps, required_columns = task
    path = Path(path_text)
    result: dict[str, Any] = {"episode_index": episode_index, "path": path_text, "errors": []}
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        rows = table.num_rows
        result["rows"] = rows
        missing = sorted(set(required_columns) - set(table.column_names))
        if missing:
            result["errors"].append(f"missing columns: {missing}")
        if rows <= 0:
            result["errors"].append("contains no rows")
        if expected_length >= 0 and rows != expected_length:
            result["errors"].append(f"row count {rows} != episodes.jsonl length {expected_length}")
        for name in required_columns:
            if name not in table.column_names:
                continue
            if table[name].null_count:
                result["errors"].append(f"column {name} contains {table[name].null_count} null values")
        if not missing and rows:
            episode_values = table["episode_index"].combine_chunks().to_pylist()
            if any(value != episode_index for value in episode_values):
                result["errors"].append("episode_index column contains an incorrect value")
            frame_values = table["frame_index"].combine_chunks().to_pylist()
            if frame_values != list(range(rows)):
                result["errors"].append("frame_index is not exactly 0..row_count-1")
            index_values = table["index"].combine_chunks().to_pylist()
            if index_values != list(range(rows)):
                result["errors"].append("index is not exactly 0..row_count-1")
            timestamps = table["timestamp"].combine_chunks()
            finite = pc.all(pc.is_finite(timestamps)).as_py() if pa.types.is_floating(timestamps.type) else False
            timestamp_values = timestamps.to_pylist()
            if not finite:
                result["errors"].append("timestamp contains non-finite values or has a non-floating type")
            elif abs(float(timestamp_values[0])) > 1e-5:
                result["errors"].append(f"timestamp starts at {timestamp_values[0]}, not 0")
            elif rows > 1 and fps > 0:
                tolerance = max(1e-4, 0.005 / fps)
                bad_steps = sum(
                    abs(float(timestamp_values[i]) - (i / fps)) > tolerance for i in range(rows)
                )
                if bad_steps:
                    result["errors"].append(f"{bad_steps} timestamps do not match frame_index/fps")
    except Exception as exc:
        result["errors"].append(f"cannot fully read Parquet: {type(exc).__name__}: {exc}")
    return result


def video_check(task: tuple[str, int, str, int, dict[str, Any]]) -> dict[str, Any]:
    path_text, episode_index, video_key, expected_frames, feature = task
    result: dict[str, Any] = {
        "episode_index": episode_index,
        "video_key": video_key,
        "path": path_text,
        "errors": [],
    }
    try:
        import av

        with av.open(path_text, mode="r") as container:
            if not container.streams.video:
                raise ValueError("no video stream")
            stream = container.streams.video[0]
            decoded = 0
            bad_size = 0
            for frame in container.decode(stream):
                decoded += 1
                if frame.width != stream.width or frame.height != stream.height:
                    bad_size += 1
            result.update(
                decoded_frames=decoded,
                codec=stream.codec_context.name,
                width=stream.width,
                height=stream.height,
                fps=float(stream.average_rate) if stream.average_rate else None,
            )
            if decoded != expected_frames:
                result["errors"].append(f"decoded frames {decoded} != Parquet rows {expected_frames}")
            if bad_size:
                result["errors"].append(f"{bad_size} decoded frames have inconsistent dimensions")
            expected_height = feature.get("info", {}).get("video.height")
            expected_width = feature.get("info", {}).get("video.width")
            expected_fps = feature.get("info", {}).get("video.fps")
            expected_codec = feature.get("info", {}).get("video.codec")
            if expected_height is not None and stream.height != expected_height:
                result["errors"].append(f"height {stream.height} != metadata {expected_height}")
            if expected_width is not None and stream.width != expected_width:
                result["errors"].append(f"width {stream.width} != metadata {expected_width}")
            if expected_fps is not None and result["fps"] is not None and abs(result["fps"] - expected_fps) > 0.01:
                result["errors"].append(f"fps {result['fps']} != metadata {expected_fps}")
            codec_aliases = {"av1": {"av1", "libdav1d"}}
            allowed = codec_aliases.get(str(expected_codec), {str(expected_codec)})
            if expected_codec and result["codec"] not in allowed:
                result["errors"].append(f"codec {result['codec']} != metadata {expected_codec}")
    except Exception as exc:
        result["errors"].append(f"cannot fully decode video: {type(exc).__name__}: {exc}")
    return result


def torch_check(
    task: tuple[str, str, int | None, str | None, int | None, float | None, float | None]
) -> dict[str, Any]:
    path_text, kind, episode_index, expected_uuid, expected_frames, source_fps, target_fps = task
    result: dict[str, Any] = {"path": path_text, "kind": kind, "errors": []}
    try:
        import torch

        value = torch.load(path_text, map_location="cpu", weights_only=True)
        if not isinstance(value, dict):
            result["errors"].append(f"cache object is {type(value).__name__}, not dict")
            return result
        if kind == "latent":
            if "latent" not in value or not hasattr(value["latent"], "shape"):
                result["errors"].append("missing tensor field 'latent'")
            if expected_frames is not None and value.get("end_frame") != expected_frames:
                result["errors"].append(f"end_frame {value.get('end_frame')} != Parquet rows {expected_frames}")
            if value.get("start_frame") != 0:
                result["errors"].append(f"start_frame {value.get('start_frame')} != 0")
            frame_ids = value.get("frame_ids")
            video_num_frames = value.get("video_num_frames")
            if not isinstance(frame_ids, list) or not frame_ids:
                result["errors"].append("frame_ids is missing or empty")
            else:
                if video_num_frames != len(frame_ids):
                    result["errors"].append(
                        f"video_num_frames {video_num_frames} != len(frame_ids) {len(frame_ids)}"
                    )
                if any(not isinstance(item, int) for item in frame_ids):
                    result["errors"].append("frame_ids contains a non-integer value")
                elif expected_frames is not None:
                    if frame_ids[0] != 0 or frame_ids[-1] >= expected_frames:
                        result["errors"].append("frame_ids falls outside the source frame range")
                    if any(right <= left for left, right in zip(frame_ids, frame_ids[1:])):
                        result["errors"].append("frame_ids is not strictly increasing")
            cache_source_fps = value.get("ori_fps")
            cache_target_fps = value.get("fps")
            if source_fps is not None and (
                not isinstance(cache_source_fps, (int, float)) or abs(cache_source_fps - source_fps) > 1e-6
            ):
                result["errors"].append(f"ori_fps {cache_source_fps} != dataset fps {source_fps}")
            if target_fps is not None and (
                not isinstance(cache_target_fps, (int, float)) or abs(cache_target_fps - target_fps) > 1e-6
            ):
                result["errors"].append(f"fps {cache_target_fps} != dataset target_fps {target_fps}")
            if frame_ids and isinstance(source_fps, (int, float)) and isinstance(target_fps, (int, float)):
                ratio = source_fps / target_fps if target_fps > 0 else 0
                rounded_ratio = round(ratio)
                if ratio > 0 and abs(ratio - rounded_ratio) < 1e-9:
                    expected_ids = list(range(0, expected_frames or 0, rounded_ratio))
                    if frame_ids != expected_ids:
                        result["errors"].append("frame_ids does not match the declared source/target fps sampling")
            latent = value.get("latent")
            latent_num_frames = value.get("latent_num_frames")
            latent_height = value.get("latent_height")
            latent_width = value.get("latent_width")
            if hasattr(latent, "shape") and all(
                isinstance(item, int) for item in (latent_num_frames, latent_height, latent_width)
            ):
                expected_tokens = latent_num_frames * latent_height * latent_width
                if len(latent.shape) < 1 or latent.shape[0] != expected_tokens:
                    result["errors"].append(
                        f"latent token count {tuple(latent.shape)} is inconsistent with temporal/spatial dimensions"
                    )
                if isinstance(video_num_frames, int) and latent_num_frames != math.ceil(video_num_frames / 4):
                    result["errors"].append(
                        f"latent_num_frames {latent_num_frames} != ceil(video_num_frames/4)"
                    )
        else:
            if expected_uuid is not None and value.get("episode_uuid") != expected_uuid:
                result["errors"].append("embedded episode_uuid does not match filename/metadata")
            if not any(key in value for key in ("text_emb", "text_emb_en", "text_emb_ch")):
                result["errors"].append("instruction cache contains no text embedding field")
    except Exception as exc:
        result["errors"].append(f"cannot load cache: {type(exc).__name__}: {exc}")
    return result


def run_parallel(label: str, function: Any, tasks: list[Any], jobs: int) -> list[dict[str, Any]]:
    if not tasks:
        return []
    results: list[dict[str, Any]] = []
    completed = 0
    print(f"  {label}: checking {len(tasks)} files with {jobs} workers", file=sys.stderr, flush=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
        futures = [executor.submit(function, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
            completed += 1
            if completed % 100 == 0 or completed == len(tasks):
                print(f"  {label}: {completed}/{len(tasks)}", file=sys.stderr, flush=True)
    return results


def validate_dataset(root: Path, level: str, jobs: int) -> dict[str, Any]:
    started = time.monotonic()
    report: dict[str, Any] = {
        "dataset": str(root),
        "level": level,
        "status": "FAIL",
        "issues": [],
        "counts": {},
    }
    print(f"Checking {root} ({level})", file=sys.stderr, flush=True)
    if not root.is_dir():
        issue(report, "error", "missing_dataset", "Dataset directory does not exist")
        return finish_report(report, started)

    for name in REQUIRED_META:
        if not (root / "meta" / name).is_file():
            issue(report, "error", "missing_metadata", f"Missing required metadata file meta/{name}")
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        all_files = [path for path in root.rglob("*") if path.is_file()]
        report["counts"]["all_files"] = len(all_files)
        uuid_files = sorted((root / "episode_uuids").glob("*.txt")) if (root / "episode_uuids").is_dir() else []
        uuid_lines = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in uuid_files)
        report["counts"]["uuid_lines"] = uuid_lines
        issue(report, "error", "not_lerobot_dataset", "Core LeRobot metadata is absent; cannot validate episodes")
        return finish_report(report, started)

    try:
        info = load_json(info_path)
    except Exception as exc:
        issue(report, "error", "invalid_info_json", f"Cannot parse meta/info.json: {exc}")
        return finish_report(report, started)

    try:
        total_episodes = int(info["total_episodes"])
        chunks_size = int(info.get("chunks_size", info.get("chunk_size", 1000)))
        fps = float(info["fps"])
        data_pattern = str(info["data_path"])
        video_pattern = str(info["video_path"])
        features = info["features"]
    except Exception as exc:
        issue(report, "error", "invalid_info_fields", f"Required info.json field is missing or invalid: {exc}")
        return finish_report(report, started)
    expected_ids = set(range(total_episodes))
    video_features = {key: value for key, value in features.items() if value.get("dtype") == "video"}
    non_video_columns = [key for key, value in features.items() if value.get("dtype") != "video"]
    required_columns = sorted(set(CRITICAL_COLUMNS) | set(non_video_columns))
    report["declared"] = {
        key: info.get(key) for key in ("codebase_version", "total_episodes", "total_frames", "total_tasks", "total_videos", "fps")
    }
    report["counts"]["video_keys"] = len(video_features)

    meta_records: dict[str, list[dict[str, Any]]] = {}
    for filename in REQUIRED_META[1:]:
        path = root / "meta" / filename
        if not path.is_file():
            meta_records[filename] = []
            continue
        try:
            meta_records[filename] = load_jsonl(path)
        except Exception as exc:
            issue(report, "error", "invalid_jsonl", f"Cannot parse meta/{filename}: {exc}")
            meta_records[filename] = []
    episodes_by_id = check_indexed_records(report, "episodes_metadata", meta_records["episodes.jsonl"], expected_ids)
    check_indexed_records(report, "episode_stats", meta_records["episodes_stats.jsonl"], expected_ids)
    task_records = meta_records["tasks.jsonl"]
    task_ids = [record.get("task_index") for record in task_records]
    if task_ids != list(range(len(task_records))):
        issue(report, "error", "invalid_task_indexes", "tasks.jsonl task_index values are not contiguous and ordered")
    if info.get("total_tasks") is not None and len(task_records) != info["total_tasks"]:
        issue(report, "error", "task_count_mismatch", f"tasks.jsonl has {len(task_records)} records, metadata declares {info['total_tasks']}")

    metadata_uuids: list[str] = []
    for episode_index in range(total_episodes):
        record = episodes_by_id.get(episode_index, {})
        episode_uuid = record.get("episode_uuid")
        if not isinstance(episode_uuid, str):
            issue(report, "error", "missing_episode_uuid", "Episode metadata has no UUID", episode_index=episode_index)
            metadata_uuids.append("")
            continue
        try:
            uuid.UUID(episode_uuid)
        except ValueError:
            issue(report, "error", "invalid_episode_uuid", "Episode metadata UUID is invalid", episode_index=episode_index, value=episode_uuid)
        metadata_uuids.append(episode_uuid)
    duplicate_uuids = sorted(value for value, count in Counter(metadata_uuids).items() if value and count > 1)
    if duplicate_uuids:
        issue(report, "error", "duplicate_episode_uuid", f"Found {len(duplicate_uuids)} duplicate episode UUIDs", values=duplicate_uuids)
    uuid_files = sorted((root / "episode_uuids").glob("*.txt")) if (root / "episode_uuids").is_dir() else []
    if uuid_files:
        uuid_lines = [line.strip() for path in uuid_files for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        report["counts"]["uuid_lines"] = len(uuid_lines)
        if Counter(uuid_lines) != Counter(metadata_uuids):
            issue(report, "error", "uuid_list_mismatch", "episode_uuids text files do not contain exactly the metadata UUIDs")
        elif uuid_lines != metadata_uuids:
            issue(report, "warning", "uuid_list_order", "episode_uuids text files contain the correct UUID set in a different order")

    expected_parquet: dict[int, Path] = {
        index: root / format_dataset_path(data_pattern, index, chunks_size) for index in range(total_episodes)
    }
    actual_parquet_paths = sorted((root / "data").rglob("episode_*.parquet")) if (root / "data").is_dir() else []
    actual_parquet, invalid_paths = ids_from_paths(actual_parquet_paths)
    for path in invalid_paths:
        issue(report, "warning", "unrecognized_parquet", "Unrecognized Parquet filename", path=path)
    compare_episode_set(report, "parquet", set(actual_parquet), expected_ids)
    duplicates = {index: [str(path) for path in paths] for index, paths in actual_parquet.items() if len(paths) > 1}
    if duplicates:
        issue(report, "error", "duplicate_parquet", "Multiple Parquet files map to the same episode", files=duplicates)
    misplaced = [str(path) for index, paths in actual_parquet.items() for path in paths if index in expected_parquet and path != expected_parquet[index]]
    if misplaced:
        issue(report, "error", "misplaced_parquet", "Parquet files do not follow data_path", paths=misplaced)

    parquet_tasks = []
    for index in range(total_episodes):
        path = expected_parquet[index]
        if path.is_file():
            expected_length = episodes_by_id.get(index, {}).get("length", -1)
            expected_length = expected_length if isinstance(expected_length, int) else -1
            parquet_tasks.append((str(path), index, expected_length, fps, required_columns))
    parquet_results = run_parallel("Parquet", parquet_check, parquet_tasks, jobs)
    row_counts = {result["episode_index"]: result.get("rows", -1) for result in parquet_results}
    report["counts"]["parquet_files"] = len(actual_parquet_paths)
    report["counts"]["parquet_rows"] = sum(value for value in row_counts.values() if value >= 0)
    for result in parquet_results:
        for message in result["errors"]:
            issue(report, "error", "invalid_parquet", message, episode_index=result["episode_index"], path=result["path"])
    if info.get("total_frames") is not None and report["counts"]["parquet_rows"] != info["total_frames"]:
        issue(report, "error", "frame_count_mismatch", f"Parquet rows total {report['counts']['parquet_rows']}, metadata declares {info['total_frames']}")

    expected_videos: dict[tuple[int, str], Path] = {}
    for index in range(total_episodes):
        for video_key in video_features:
            expected_videos[index, video_key] = root / format_dataset_path(
                video_pattern, index, chunks_size, video_key=video_key
            )
    actual_video_paths = sorted((root / "videos").rglob("episode_*.mp4")) if (root / "videos").is_dir() else []
    report["counts"]["video_files"] = len(actual_video_paths)
    missing_videos = [str(path) for path in expected_videos.values() if not path.is_file()]
    if missing_videos:
        issue(report, "error", "missing_video", f"Missing {len(missing_videos)} declared video files", paths=missing_videos)
    expected_video_path_set = set(expected_videos.values())
    extra_videos = [str(path) for path in actual_video_paths if path not in expected_video_path_set]
    if extra_videos:
        issue(report, "error", "extra_video", f"Found {len(extra_videos)} unexpected episode videos", paths=extra_videos)
    if info.get("total_videos") is not None and len(actual_video_paths) != info["total_videos"]:
        issue(report, "error", "video_count_mismatch", f"Found {len(actual_video_paths)} videos, metadata declares {info['total_videos']}")
    if level in ("decode", "full"):
        video_tasks = [
            (str(path), index, key, row_counts.get(index, -1), video_features[key])
            for (index, key), path in expected_videos.items()
            if path.is_file() and row_counts.get(index, -1) >= 0
        ]
        video_results = run_parallel("Videos", video_check, video_tasks, jobs)
        report["counts"]["videos_fully_decoded"] = len(video_results)
        report["counts"]["decoded_video_frames"] = sum(result.get("decoded_frames", 0) for result in video_results)
        for result in video_results:
            for message in result["errors"]:
                issue(report, "error", "invalid_video", message, episode_index=result["episode_index"], video_key=result["video_key"], path=result["path"])

    if (root / "meta" / "latent_sidecars").is_dir():
        sidecar_paths = sorted((root / "meta" / "latent_sidecars").glob("episode_*_latent.json"))
        sidecars, invalid_sidecars = ids_from_paths(sidecar_paths)
        compare_episode_set(report, "latent_sidecar", set(sidecars), expected_ids)
        report["counts"]["latent_sidecars"] = len(sidecar_paths)
        for path in sidecar_paths:
            try:
                value = load_json(path)
                match = EPISODE_FILE_RE.search(path.name)
                index = int(match.group(1)) if match else None
                if index is not None and value.get("episode_index") != index:
                    issue(report, "error", "invalid_latent_sidecar", "Sidecar episode_index does not match filename", path=str(path))
                if index is not None and metadata_uuids[index] and value.get("episode_uuid") != metadata_uuids[index]:
                    issue(report, "error", "invalid_latent_sidecar", "Sidecar UUID does not match episode metadata", path=str(path))
                if set(value.get("camera_keys", [])) != set(video_features):
                    issue(report, "error", "invalid_latent_sidecar", "Sidecar camera_keys do not match video features", path=str(path))
            except Exception as exc:
                issue(report, "error", "invalid_latent_sidecar", f"Cannot parse sidecar: {exc}", path=str(path))

    cache_tasks: list[
        tuple[str, str, int | None, str | None, int | None, float | None, float | None]
    ] = []
    target_fps = float(info["target_fps"]) if info.get("target_fps") is not None else None
    if (root / "latents").is_dir():
        latent_paths = sorted((root / "latents").rglob("*.pth"))
        report["counts"]["latent_files"] = len(latent_paths)
        expected_latents = {(index, key) for index in range(total_episodes) for key in video_features}
        observed_latents: set[tuple[int, str]] = set()
        for path in latent_paths:
            match = LATENT_FILE_RE.search(path.name)
            key = path.parent.name
            if not match:
                issue(report, "error", "invalid_latent_name", "Latent filename is not recognized", path=str(path))
                continue
            index, start, end = map(int, match.groups())
            pair = (index, key)
            if pair in observed_latents:
                issue(report, "error", "duplicate_latent", "Multiple latent files map to one episode/camera", episode_index=index, video_key=key)
            observed_latents.add(pair)
            expected_frames = row_counts.get(index)
            if start != 0 or (expected_frames is not None and end != expected_frames):
                issue(report, "error", "latent_range_mismatch", f"Latent filename range {start}:{end} does not match 0:{expected_frames}", path=str(path))
            cache_tasks.append((str(path), "latent", index, None, expected_frames, fps, target_fps))
        missing = sorted(expected_latents - observed_latents)
        extra = sorted(observed_latents - expected_latents)
        if missing:
            issue(report, "error", "missing_latent", f"Missing {len(missing)} episode/camera latent files", entries=missing)
        if extra:
            issue(report, "error", "extra_latent", f"Found {len(extra)} unexpected episode/camera latent files", entries=extra)

    if (root / "instruct_emb").is_dir():
        instruction_paths = sorted((root / "instruct_emb").glob("*.pt"))
        report["counts"]["instruction_embeddings"] = len(instruction_paths)
        by_uuid = {path.stem: path for path in instruction_paths}
        expected_uuid_set = {value for value in metadata_uuids if value}
        missing = sorted(expected_uuid_set - set(by_uuid))
        extra = sorted(set(by_uuid) - expected_uuid_set)
        if missing:
            issue(report, "error", "missing_instruction_embedding", f"Missing {len(missing)} instruction embeddings", uuids=missing)
        if extra:
            issue(report, "error", "extra_instruction_embedding", f"Found {len(extra)} unexpected instruction embeddings", uuids=extra)
        cache_tasks.extend(
            (str(path), "instruction", None, value, None, None, None) for value, path in by_uuid.items()
        )

    if level == "full" and cache_tasks:
        cache_results = run_parallel("Caches", torch_check, cache_tasks, jobs)
        report["counts"]["cache_files_loaded"] = len(cache_results)
        for result in cache_results:
            for message in result["errors"]:
                issue(report, "error", "invalid_cache", message, path=result["path"], cache_kind=result["kind"])

    return finish_report(report, started)


def print_summary(report: dict[str, Any], max_issues: int = 50) -> None:
    print(f"\n[{report['status']}] {report['dataset']}")
    declared = report.get("declared", {})
    counts = report.get("counts", {})
    print(
        "  episodes: declared={0}, parquet={1}; frames: declared={2}, parquet={3}; videos: declared={4}, found={5}, decoded={6}".format(
            declared.get("total_episodes", "?"),
            counts.get("parquet_files", 0),
            declared.get("total_frames", "?"),
            counts.get("parquet_rows", 0),
            declared.get("total_videos", "?"),
            counts.get("video_files", 0),
            counts.get("videos_fully_decoded", "not run"),
        )
    )
    optional = [
        f"latents={counts['latent_files']}" for key in ("latent_files",) if key in counts
    ] + [
        f"instruction_embeddings={counts['instruction_embeddings']}" for key in ("instruction_embeddings",) if key in counts
    ] + [
        f"sidecars={counts['latent_sidecars']}" for key in ("latent_sidecars",) if key in counts
    ] + [
        f"caches_loaded={counts['cache_files_loaded']}" for key in ("cache_files_loaded",) if key in counts
    ]
    if optional:
        print("  optional caches: " + ", ".join(optional))
    print(f"  issues: errors={report.get('error_count', sum(i['severity'] == 'error' for i in report['issues']))}, warnings={report.get('warning_count', sum(i['severity'] == 'warning' for i in report['issues']))}; elapsed={report['elapsed_seconds']}s")
    for item in report["issues"][:max_issues]:
        location = ""
        if "episode_index" in item:
            location += f" episode={item['episode_index']}"
        if "video_key" in item:
            location += f" key={item['video_key']}"
        if "path" in item:
            location += f" path={item['path']}"
        print(f"  {item['severity'].upper()} {item['code']}:{location} {item['message']}")
    hidden = len(report["issues"]) - max_issues
    if hidden > 0:
        print(f"  ... {hidden} additional issues omitted from console; see the JSON report")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+", type=Path, help="LeRobot dataset root(s) on this machine")
    parser.add_argument("--level", choices=("structure", "decode", "full"), default="full")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel worker processes (default: 4)")
    parser.add_argument("--json-out", type=Path, help="Write the complete report list as JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.jobs < 1:
        print("--jobs must be at least 1", file=sys.stderr)
        return 2
    try:
        import pyarrow  # noqa: F401
        if args.level in ("decode", "full"):
            import av  # noqa: F401
        if args.level == "full":
            import torch  # noqa: F401
    except ImportError as exc:
        print(f"Missing dependency for --level {args.level}: {exc}", file=sys.stderr)
        return 2
    reports = [validate_dataset(path.resolve(), args.level, args.jobs) for path in args.datasets]
    for report in reports:
        print_summary(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("w", encoding="utf-8") as handle:
            json.dump(reports, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"\nJSON report: {args.json_out}")
    return 1 if any(report["status"] != "PASS" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
