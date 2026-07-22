#!/usr/bin/env python3
"""Append newly accepted QC episodes to an existing filtered LeRobot dataset.

This is intentionally append-only. It refuses to run if any episode already in
the filtered output is no longer accepted in the provided label snapshot.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            json.dump(row, handle, ensure_ascii=False)
            handle.write("\n")
    os.replace(tmp, path)


def qc_dataset_id(source: Path) -> str:
    basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name).strip("_") or "dataset"
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:12]
    return f"{basename}-{digest}"


def chunk_for_episode(index: int, chunks_size: int) -> int:
    return index // chunks_size


def load_sqlite_labels(labels_path: Path, dataset_key: str) -> tuple[dict[int, str], dict[int, list[dict[str, Any]]]]:
    if labels_path.suffix != ".db":
        raise SystemExit("incremental append currently expects a SQLite labels.db snapshot")
    with sqlite3.connect(labels_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(labels)").fetchall()}
        uuid_expr = "episode_uuid" if "episode_uuid" in columns else "'' AS episode_uuid"
        rows = conn.execute(
            f"""
            SELECT episode_index, user, status, updated_at, {uuid_expr}
            FROM labels
            WHERE dataset_id = ?
            ORDER BY episode_index
            """,
            (dataset_key,),
        ).fetchall()
    status_by_label_index: dict[int, str] = {}
    labels_by_label_index: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        episode_index = int(row["episode_index"])
        status_by_label_index[episode_index] = str(row["status"])
        labels_by_label_index.setdefault(episode_index, []).append(
            {
                "user": row["user"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "episode_uuid": row["episode_uuid"],
            }
        )
    return status_by_label_index, labels_by_label_index


def resolve_labels_to_source(
    source_episodes: dict[int, dict[str, Any]],
    status_by_label_index: dict[int, str],
    labels_by_label_index: dict[int, list[dict[str, Any]]],
) -> tuple[dict[int, str], dict[int, list[dict[str, Any]]], dict[str, Any]]:
    uuid_to_source: dict[str, int] = {}
    for source_index, episode in source_episodes.items():
        uuid = str(episode.get("episode_uuid") or "")
        if uuid:
            if uuid in uuid_to_source:
                raise SystemExit(f"duplicate source episode_uuid: {uuid}")
            uuid_to_source[uuid] = source_index

    has_label_uuids = any(
        any(str(label.get("episode_uuid") or "") for label in labels)
        for labels in labels_by_label_index.values()
    )
    status_by_orig: dict[int, str] = {}
    labels_by_orig: dict[int, list[dict[str, Any]]] = {}
    missing_label_uuids: list[dict[str, Any]] = []
    index_uuid_mismatches: list[dict[str, Any]] = []

    for label_index, status in status_by_label_index.items():
        labels = labels_by_label_index.get(label_index, [])
        label_uuid = next((str(label.get("episode_uuid") or "") for label in labels if label.get("episode_uuid")), "")
        if label_uuid:
            if label_uuid not in uuid_to_source:
                missing_label_uuids.append({"label_episode_index": label_index, "episode_uuid": label_uuid})
                continue
            source_index = uuid_to_source[label_uuid]
            same_index_uuid = str(source_episodes.get(label_index, {}).get("episode_uuid") or "")
            if same_index_uuid and same_index_uuid != label_uuid:
                index_uuid_mismatches.append(
                    {
                        "label_episode_index": label_index,
                        "source_episode_index": source_index,
                        "label_episode_uuid": label_uuid,
                        "source_uuid_at_same_index": same_index_uuid,
                    }
                )
        elif has_label_uuids:
            missing_label_uuids.append({"label_episode_index": label_index, "episode_uuid": ""})
            continue
        else:
            if label_index not in source_episodes:
                missing_label_uuids.append({"label_episode_index": label_index, "episode_uuid": ""})
                continue
            source_index = label_index

        status_by_orig[source_index] = status
        labels_by_orig[source_index] = labels

    if missing_label_uuids:
        raise SystemExit(f"label episode_uuid values missing from source dataset: {missing_label_uuids[:10]}")

    match_summary = {
        "match_mode": "uuid" if has_label_uuids else "index",
        "source_episode_count": len(source_episodes),
        "label_episode_count": len(status_by_label_index),
        "matched_episode_count": len(status_by_orig),
        "index_uuid_mismatch_count": len(index_uuid_mismatches),
        "index_uuid_mismatch_examples": index_uuid_mismatches[:20],
    }
    return status_by_orig, labels_by_orig, match_summary


def remap_table(path: Path, new_episode_index: int, global_start: int) -> pa.Table:
    table = pq.read_table(path)
    names = set(table.column_names)
    if "episode_index" in names:
        table = table.set_column(
            table.schema.get_field_index("episode_index"),
            "episode_index",
            pa.array([new_episode_index] * table.num_rows, type=table.schema.field("episode_index").type),
        )
    if "index" in names:
        table = table.set_column(
            table.schema.get_field_index("index"),
            "index",
            pa.array(range(global_start, global_start + table.num_rows), type=table.schema.field("index").type),
        )
    return table


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_episode_video_files(source: Path, output: Path, old_index: int, new_index: int, chunks_size: int) -> int:
    old_chunk = chunk_for_episode(old_index, chunks_size)
    new_chunk = chunk_for_episode(new_index, chunks_size)
    src_chunk = source / "videos" / f"chunk-{old_chunk:03d}"
    if not src_chunk.exists():
        return 0
    copied = 0
    for camera_dir in sorted(src_chunk.iterdir()):
        if not camera_dir.is_dir():
            continue
        src = camera_dir / f"episode_{old_index:06d}.mp4"
        if not src.exists():
            continue
        dst = output / "videos" / f"chunk-{new_chunk:03d}" / camera_dir.name / f"episode_{new_index:06d}.mp4"
        copy_file(src, dst)
        copied += 1
    return copied


def copy_episode_latent_files(
    source: Path,
    output: Path,
    old_index: int,
    new_index: int,
    old_length: int,
    new_length: int,
    chunks_size: int,
) -> int:
    old_chunk = chunk_for_episode(old_index, chunks_size)
    new_chunk = chunk_for_episode(new_index, chunks_size)
    src_chunk = source / "latents" / f"chunk-{old_chunk:03d}"
    if not src_chunk.exists():
        return 0
    copied = 0
    for camera_dir in sorted(src_chunk.iterdir()):
        if not camera_dir.is_dir():
            continue
        src = camera_dir / f"episode_{old_index:06d}_0_{old_length}.pth"
        if not src.exists():
            matches = list(camera_dir.glob(f"episode_{old_index:06d}_0_*.pth"))
            if not matches:
                continue
            src = matches[0]
        dst = output / "latents" / f"chunk-{new_chunk:03d}" / camera_dir.name / f"episode_{new_index:06d}_0_{new_length}.pth"
        copy_file(src, dst)
        copied += 1
    return copied


def update_sidecar(sidecar: dict[str, Any], new_index: int, chunks_size: int) -> dict[str, Any]:
    new_chunk = chunk_for_episode(new_index, chunks_size)
    sidecar["episode_index"] = new_index
    video_uris = sidecar.get("video_uris")
    if isinstance(video_uris, dict):
        for key in list(video_uris):
            camera = Path(video_uris[key]).parent.name
            video_uris[key] = f"videos/chunk-{new_chunk:03d}/{camera}/episode_{new_index:06d}.mp4"
    return sidecar


def process_episode_asset(task: dict[str, Any]) -> dict[str, Any]:
    source: Path = task["source"]
    output: Path = task["output"]
    chunks_size = int(task["chunks_size"])
    old_index = int(task["old_index"])
    new_index = int(task["new_index"])
    old_length = int(task["old_length"])
    global_start = int(task["global_start"])

    old_chunk = chunk_for_episode(old_index, chunks_size)
    new_chunk = chunk_for_episode(new_index, chunks_size)
    src_data = source / "data" / f"chunk-{old_chunk:03d}" / f"episode_{old_index:06d}.parquet"
    dst_data = output / "data" / f"chunk-{new_chunk:03d}" / f"episode_{new_index:06d}.parquet"
    if dst_data.exists():
        raise RuntimeError(f"destination parquet already exists: {dst_data}")
    dst_data.parent.mkdir(parents=True, exist_ok=True)
    table = remap_table(src_data, new_index, global_start)
    if table.num_rows != old_length:
        raise RuntimeError(f"episode {old_index} length mismatch: metadata={old_length}, parquet={table.num_rows}")
    pq.write_table(table, dst_data)

    videos = copy_episode_video_files(source, output, old_index, new_index, chunks_size)
    latents = copy_episode_latent_files(source, output, old_index, new_index, old_length, old_length, chunks_size)

    src_sidecar = source / "meta" / "latent_sidecars" / f"episode_{old_index:06d}_latent.json"
    sidecar_written = False
    if src_sidecar.exists():
        sidecar = update_sidecar(read_json(src_sidecar), new_index, chunks_size)
        sidecar["original_episode_index"] = old_index
        write_json(output / "meta" / "latent_sidecars" / f"episode_{new_index:06d}_latent.json", sidecar)
        sidecar_written = True

    emb_copied = False
    episode_uuid = task.get("episode_uuid")
    if episode_uuid:
        src_emb = source / "instruct_emb" / f"{episode_uuid}.pt"
        dst_emb = output / "instruct_emb" / src_emb.name
        if src_emb.exists() and not dst_emb.exists():
            copy_file(src_emb, dst_emb)
            emb_copied = True
    return {
        "new_index": new_index,
        "old_index": old_index,
        "rows": table.num_rows,
        "videos": videos,
        "latents": latents,
        "sidecar": sidecar_written,
        "embedding": emb_copied,
    }


def backup_meta(output: Path, stamp: str) -> str:
    backup = output / "meta" / f".incremental_backup_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for rel in ["info.json", "episodes.jsonl", "episodes_stats.jsonl", "qc_filter_accept_mapping.jsonl", "qc_filter_summary.json"]:
        src = output / "meta" / rel
        if src.exists():
            shutil.copy2(src, backup / rel)
    uuid_file = output / "episode_uuids" / "episode_uuids_part_001.txt"
    if uuid_file.exists():
        (backup / "episode_uuids").mkdir(exist_ok=True)
        shutil.copy2(uuid_file, backup / "episode_uuids" / uuid_file.name)
    return str(backup)


def append_to_dataset(
    *,
    output: Path,
    source: Path,
    source_info: dict[str, Any],
    source_episodes: dict[int, dict[str, Any]],
    source_stats: dict[int, dict[str, Any]],
    append_items: list[dict[str, Any]],
    labels_by_orig: dict[int, list[dict[str, Any]]],
    rejected_count: int,
    match_summary: dict[str, Any],
    labels_path: Path,
    workers: int,
    stamp: str,
) -> dict[str, Any]:
    if not append_items:
        info = read_json(output / "meta" / "info.json")
        return {"path": str(output), "added": 0, "episodes": info.get("total_episodes"), "frames": info.get("total_frames")}

    chunks_size = int(source_info.get("chunks_size") or 1000)
    info = read_json(output / "meta" / "info.json")
    episodes = read_jsonl(output / "meta" / "episodes.jsonl")
    stats = read_jsonl(output / "meta" / "episodes_stats.jsonl")
    mapping = read_jsonl(output / "meta" / "qc_filter_accept_mapping.jsonl")
    summary = read_json(output / "meta" / "qc_filter_summary.json")
    start_index = len(episodes)
    global_start = int(info.get("total_frames") or sum(int(row.get("length") or 0) for row in episodes))

    new_episodes: list[dict[str, Any]] = []
    new_stats: list[dict[str, Any]] = []
    new_mapping: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for offset, item in enumerate(append_items):
        old_index = int(item["source_episode_index"])
        old_episode = dict(source_episodes[old_index])
        old_length = int(old_episode.get("length") or 0)
        new_index = start_index + offset

        episode_row = dict(old_episode)
        episode_row["original_episode_index"] = old_index
        episode_row["episode_index"] = new_index
        new_episodes.append(episode_row)

        if old_index in source_stats:
            stat_row = dict(source_stats[old_index])
            stat_row["original_episode_index"] = old_index
            stat_row["episode_index"] = new_index
            new_stats.append(stat_row)

        new_mapping.append(
            {
                "new_episode_index": new_index,
                "original_episode_index": old_index,
                "label_episode_index": item.get("label_episode_index", old_index),
                "episode_uuid": old_episode.get("episode_uuid"),
                "final_status": "accept",
                "match_mode": item.get("match_mode", match_summary.get("match_mode", "uuid")),
                "labels": labels_by_orig.get(old_index, []),
            }
        )
        tasks.append(
            {
                "source": source,
                "output": output,
                "chunks_size": chunks_size,
                "old_index": old_index,
                "new_index": new_index,
                "old_length": old_length,
                "global_start": global_start,
                "episode_uuid": old_episode.get("episode_uuid"),
            }
        )
        global_start += old_length

    backup = backup_meta(output, stamp)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process_episode_asset, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    episodes.extend(new_episodes)
    stats.extend(new_stats)
    mapping.extend(new_mapping)

    info["total_episodes"] = len(episodes)
    info["total_frames"] = sum(int(row.get("length") or 0) for row in episodes)
    info["total_videos"] = len(episodes) * 3
    info["total_chunks"] = max(1, math.ceil(len(episodes) / chunks_size))
    info["splits"] = {"train": f"0:{len(episodes)}"}
    write_json(output / "meta" / "info.json", info)
    write_jsonl(output / "meta" / "episodes.jsonl", episodes)
    write_jsonl(output / "meta" / "episodes_stats.jsonl", stats)
    write_jsonl(output / "meta" / "qc_filter_accept_mapping.jsonl", mapping)
    with (output / "episode_uuids" / "episode_uuids_part_001.txt").open("w", encoding="utf-8") as handle:
        for row in episodes:
            handle.write(str(row.get("episode_uuid", "")))
            handle.write("\n")

    summary["labels"] = str(labels_path)
    summary["accepted"] = len(mapping)
    summary["accepted_total_before_limit"] = len(mapping)
    summary["rejected"] = rejected_count
    summary["limit_accepted"] = None
    summary["match"] = match_summary
    summary["accepted_original_episode_indices"] = [int(row["original_episode_index"]) for row in mapping]
    summary["accepted_label_episode_indices"] = [int(row.get("label_episode_index", row["original_episode_index"])) for row in mapping]
    summary["incremental_update"] = {
        "updated_at": stamp,
        "added": len(new_episodes),
        "added_original_episode_indices": [int(row["original_episode_index"]) for row in new_mapping],
        "previous_total_episodes": start_index,
        "new_total_episodes": len(episodes),
        "backup_dir": backup,
    }
    write_json(output / "meta" / "qc_filter_summary.json", summary)
    return {"path": str(output), "added": len(new_episodes), "episodes": len(episodes), "frames": info["total_frames"], "backup": backup}


def dataset_summary(output: Path) -> dict[str, Any]:
    info = read_json(output / "meta" / "info.json")
    mapping = read_jsonl(output / "meta" / "qc_filter_accept_mapping.jsonl")
    videos = list((output / "videos").glob("chunk-*/*/episode_*.mp4"))
    latents = list((output / "latents").glob("chunk-*/*/*.pth"))
    return {
        "path": str(output),
        "episodes": info.get("total_episodes"),
        "frames": info.get("total_frames"),
        "seconds": info.get("total_frames") / float(info.get("fps") or 30),
        "hours": info.get("total_frames") / float(info.get("fps") or 30) / 3600,
        "videos": len(videos),
        "latents": len(latents),
        "original_episode_min": min(int(row["original_episode_index"]) for row in mapping) if mapping else None,
        "original_episode_max": max(int(row["original_episode_index"]) for row in mapping) if mapping else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path, help="SQLite labels.db snapshot")
    parser.add_argument("--output", required=True, type=Path, help="existing accepted-only output dataset")
    parser.add_argument("--part-count", type=int, default=0, help="also append to output_part01..N; use 5 for five shards")
    parser.add_argument("--part-prefix", default="", help="default: output name plus '_part'")
    parser.add_argument("--summary", type=Path, default=None, help="split summary JSON path")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    labels = args.labels.resolve()
    output = args.output.resolve()
    if not output.exists():
        raise SystemExit(f"existing output dataset does not exist: {output}")

    source_info = read_json(source / "meta" / "info.json")
    source_episodes = {int(row["episode_index"]): row for row in read_jsonl(source / "meta" / "episodes.jsonl")}
    source_stats = {int(row["episode_index"]): row for row in read_jsonl(source / "meta" / "episodes_stats.jsonl")}
    dataset_key = qc_dataset_id(source)
    status_by_label_index, labels_by_label_index = load_sqlite_labels(labels, dataset_key)
    status_by_orig, labels_by_orig, match_summary = resolve_labels_to_source(source_episodes, status_by_label_index, labels_by_label_index)
    status_by_uuid = {
        str(source_episodes[orig].get("episode_uuid") or ""): status
        for orig, status in status_by_orig.items()
        if source_episodes.get(orig, {}).get("episode_uuid")
    }

    mapping = read_jsonl(output / "meta" / "qc_filter_accept_mapping.jsonl")
    existing_orig = {int(row["original_episode_index"]) for row in mapping}
    existing_uuid = {str(row.get("episode_uuid") or "") for row in mapping}
    stale_existing = []
    for row in mapping:
        orig = int(row["original_episode_index"])
        uuid = str(row.get("episode_uuid") or "")
        status = status_by_uuid.get(uuid) if uuid else status_by_orig.get(orig)
        if status != "accept":
            stale_existing.append({"new": row.get("new_episode_index"), "original": orig, "episode_uuid": uuid, "status": status})
    if stale_existing:
        raise SystemExit(f"existing output contains episodes no longer accepted; rebuild instead of appending: {stale_existing[:10]}")

    new_items = []
    for orig, status in sorted(status_by_orig.items()):
        if status != "accept":
            continue
        uuid = str(source_episodes[orig].get("episode_uuid") or "")
        if orig in existing_orig or uuid in existing_uuid:
            continue
        new_items.append({"source_episode_index": orig, "label_episode_index": orig, "episode_uuid": uuid, "match_mode": match_summary["match_mode"]})

    added_frames = sum(int(source_episodes[item["source_episode_index"]].get("length") or 0) for item in new_items)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "source": str(source),
                    "output": str(output),
                    "current_output_episodes": len(mapping),
                    "current_label_accept": sum(1 for status in status_by_orig.values() if status == "accept"),
                    "new_accept_count": len(new_items),
                    "new_frames": added_frames,
                    "new_hours": added_frames / float(source_info.get("fps") or 30) / 3600,
                    "new_original_episode_indices": [item["source_episode_index"] for item in new_items],
                    "match": match_summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    stamp = time.strftime("%Y%m%d_%H%M%S")
    rejected_count = sum(1 for status in status_by_orig.values() if status == "reject")
    results = [
        append_to_dataset(
            output=output,
            source=source,
            source_info=source_info,
            source_episodes=source_episodes,
            source_stats=source_stats,
            append_items=new_items,
            labels_by_orig=labels_by_orig,
            rejected_count=rejected_count,
            match_summary=match_summary,
            labels_path=labels,
            workers=args.workers,
            stamp=stamp,
        )
    ]

    part_paths: list[Path] = []
    part_distribution = []
    if args.part_count:
        prefix = args.part_prefix or f"{output.name}_part"
        part_paths = [output.parent / f"{prefix}{index:02d}" for index in range(1, args.part_count + 1)]
        for path in part_paths:
            if not path.exists():
                raise SystemExit(f"part dataset does not exist: {path}")
        part_loads = [
            {"path": path, "frames": int(read_json(path / "meta" / "info.json").get("total_frames") or 0), "items": []}
            for path in part_paths
        ]
        for item in sorted(new_items, key=lambda row: int(source_episodes[row["source_episode_index"]].get("length") or 0), reverse=True):
            target = min(part_loads, key=lambda row: row["frames"])
            target["items"].append(item)
            target["frames"] += int(source_episodes[item["source_episode_index"]].get("length") or 0)
        for load in part_loads:
            load["items"].sort(key=lambda row: row["source_episode_index"])
            results.append(
                append_to_dataset(
                    output=load["path"],
                    source=source,
                    source_info=source_info,
                    source_episodes=source_episodes,
                    source_stats=source_stats,
                    append_items=load["items"],
                    labels_by_orig=labels_by_orig,
                    rejected_count=rejected_count,
                    match_summary=match_summary,
                    labels_path=labels,
                    workers=args.workers,
                    stamp=stamp,
                )
            )
            part_distribution.append(
                {
                    "path": str(load["path"]),
                    "added": len(load["items"]),
                    "added_original_episode_indices": [item["source_episode_index"] for item in load["items"]],
                }
            )

    summary_path = args.summary or output.parent / f"{output.name}_split_summary.json"
    combined = {
        "source_dataset": str(source),
        "base_dataset": str(output),
        "updated_at": stamp,
        "incremental_added": len(new_items),
        "incremental_added_frames": added_frames,
        "incremental_added_original_episode_indices": [item["source_episode_index"] for item in new_items],
        "base": dataset_summary(output),
        "parts": [dataset_summary(path) for path in part_paths],
        "part_incremental_distribution": part_distribution,
        "results": results,
        "match": match_summary,
    }
    write_json(summary_path, combined)
    print(json.dumps(combined, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
