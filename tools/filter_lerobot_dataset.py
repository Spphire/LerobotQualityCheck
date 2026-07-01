#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


EPISODE_RE = re.compile(r"episode_(\d{6})(?:_0_(\d+))?(\.[^.]+)$")


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def final_episode_status_from_json(labels_path):
    store = read_json(labels_path)
    by_episode = {}
    global_labels = store.get("labels") if isinstance(store.get("labels"), dict) else {}
    for key, label in global_labels.items():
        if not isinstance(label, dict):
            continue
        status = label.get("status")
        if status not in {"accept", "reject"}:
            continue
        try:
            episode_index = int(key)
        except ValueError:
            continue
        user = label.get("user") or label.get("annotator") or ""
        by_episode[episode_index] = [
            {
                "user": user,
                "status": status,
                "episode_uuid": label.get("episode_uuid", ""),
                "updated_at": label.get("updated_at"),
            }
        ]
    if by_episode:
        final = {episode_index: labels[0]["status"] for episode_index, labels in by_episode.items()}
        return final, by_episode

    for user, labels in (store.get("labels_by_user") or {}).items():
        if not isinstance(labels, dict):
            continue
        for key, label in labels.items():
            status = label.get("status")
            if status not in {"accept", "reject"}:
                continue
            try:
                episode_index = int(key)
            except ValueError:
                continue
            by_episode.setdefault(episode_index, []).append(
                {
                    "user": user,
                    "status": status,
                    "episode_uuid": label.get("episode_uuid", ""),
                    "updated_at": label.get("updated_at"),
                }
            )
    final = {}
    for episode_index, labels in by_episode.items():
        statuses = {label["status"] for label in labels}
        final[episode_index] = "reject" if "reject" in statuses else "accept"
    return final, by_episode


def final_episode_status_from_sqlite(labels_path):
    by_episode = {}
    with sqlite3.connect(labels_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(labels)").fetchall()}
        uuid_expr = "episode_uuid" if "episode_uuid" in columns else "'' AS episode_uuid"
        rows = conn.execute(
            f"""
            SELECT episode_index, user, status, updated_at, {uuid_expr}
            FROM labels
            WHERE status IN ('accept', 'reject')
            """
        ).fetchall()
    for row in rows:
        episode_index = int(row["episode_index"])
        by_episode.setdefault(episode_index, []).append(
            {
                "user": row["user"],
                "status": row["status"],
                "episode_uuid": row["episode_uuid"],
                "updated_at": row["updated_at"],
            }
        )
    final = {}
    for episode_index, labels in by_episode.items():
        statuses = {label["status"] for label in labels}
        final[episode_index] = "reject" if "reject" in statuses else "accept"
    return final, by_episode


def final_episode_status(labels_path):
    if labels_path.suffix == ".db":
        return final_episode_status_from_sqlite(labels_path)
    return final_episode_status_from_json(labels_path)


def first_label_uuid(labels):
    for label in labels:
        uuid = label.get("episode_uuid")
        if uuid:
            return str(uuid)
    return ""


def resolve_episode_matches(episodes, final_status, labels_by_episode):
    uuid_to_source_index = {}
    duplicate_source_uuids = []
    for source_index, episode in episodes.items():
        uuid = episode.get("episode_uuid")
        if not uuid:
            continue
        uuid = str(uuid)
        if uuid in uuid_to_source_index:
            duplicate_source_uuids.append(
                {
                    "episode_uuid": uuid,
                    "source_episode_indices": [uuid_to_source_index[uuid], source_index],
                }
            )
            continue
        uuid_to_source_index[uuid] = source_index
    if duplicate_source_uuids:
        raise SystemExit(f"Duplicate source episode_uuid values: {duplicate_source_uuids[:5]}")

    has_label_uuids = any(first_label_uuid(labels_by_episode.get(index, [])) for index in final_status)
    missing_label_uuids = []
    duplicate_matches = []
    index_uuid_mismatches = []
    matches_by_source_index = {}

    for label_episode_index, status in final_status.items():
        labels = labels_by_episode.get(label_episode_index, [])
        label_uuid = first_label_uuid(labels)
        if label_uuid:
            source_episode_index = uuid_to_source_index.get(label_uuid)
            if source_episode_index is None:
                missing_label_uuids.append(
                    {
                        "label_episode_index": label_episode_index,
                        "episode_uuid": label_uuid,
                    }
                )
                continue
            if label_episode_index in episodes:
                source_uuid_at_label_index = str(episodes[label_episode_index].get("episode_uuid", "") or "")
                if source_uuid_at_label_index and source_uuid_at_label_index != label_uuid:
                    index_uuid_mismatches.append(
                        {
                            "label_episode_index": label_episode_index,
                            "label_episode_uuid": label_uuid,
                            "source_uuid_at_same_index": source_uuid_at_label_index,
                            "matched_source_episode_index": source_episode_index,
                        }
                    )
            match_mode = "uuid"
        else:
            if has_label_uuids:
                missing_label_uuids.append(
                    {
                        "label_episode_index": label_episode_index,
                        "episode_uuid": "",
                    }
                )
                continue
            source_episode_index = label_episode_index
            if source_episode_index not in episodes:
                missing_label_uuids.append(
                    {
                        "label_episode_index": label_episode_index,
                        "episode_uuid": "",
                    }
                )
                continue
            match_mode = "index"

        match = {
            "source_episode_index": source_episode_index,
            "label_episode_index": label_episode_index,
            "episode_uuid": label_uuid or episodes[source_episode_index].get("episode_uuid", ""),
            "status": status,
            "labels": labels,
            "match_mode": match_mode,
        }
        existing = matches_by_source_index.get(source_episode_index)
        if existing is not None:
            duplicate_matches.append([existing, match])
            continue
        matches_by_source_index[source_episode_index] = match

    if missing_label_uuids:
        raise SystemExit(f"Label episode_uuid values missing from source dataset: {missing_label_uuids[:10]}")
    if duplicate_matches:
        raise SystemExit(f"Multiple labels resolved to the same source episode: {duplicate_matches[:5]}")

    matches = list(matches_by_source_index.values())
    accepted_matches = sorted(
        (match for match in matches if match["status"] == "accept"),
        key=lambda match: match["source_episode_index"],
    )
    rejected_matches = sorted(
        (match for match in matches if match["status"] == "reject"),
        key=lambda match: match["source_episode_index"],
    )
    match_summary = {
        "match_mode": "uuid" if has_label_uuids else "index",
        "source_episode_count": len(episodes),
        "label_episode_count": len(final_status),
        "matched_episode_count": len(matches),
        "index_uuid_mismatch_count": len(index_uuid_mismatches),
        "index_uuid_mismatch_examples": index_uuid_mismatches[:20],
    }
    return accepted_matches, rejected_matches, match_summary


def chunk_for_episode(index, chunks_size):
    return index // chunks_size


def remap_table(path, new_episode_index, global_start):
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


def copy_or_link(src, dst, hardlink=False):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if hardlink:
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def copy_episode_video_files(src_root, dst_root, old_index, new_index, chunks_size):
    old_chunk = chunk_for_episode(old_index, chunks_size)
    new_chunk = chunk_for_episode(new_index, chunks_size)
    src_chunk = src_root / "videos" / f"chunk-{old_chunk:03d}"
    if not src_chunk.exists():
        return 0
    copied = 0
    for camera_dir in sorted(src_chunk.iterdir()):
        if not camera_dir.is_dir():
            continue
        src = camera_dir / f"episode_{old_index:06d}.mp4"
        if not src.exists():
            continue
        dst = dst_root / "videos" / f"chunk-{new_chunk:03d}" / camera_dir.name / f"episode_{new_index:06d}.mp4"
        copy_or_link(src, dst)
        copied += 1
    return copied


def copy_episode_latent_files(src_root, dst_root, old_index, new_index, old_length, new_length, chunks_size):
    old_chunk = chunk_for_episode(old_index, chunks_size)
    new_chunk = chunk_for_episode(new_index, chunks_size)
    src_chunk = src_root / "latents" / f"chunk-{old_chunk:03d}"
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
        dst = dst_root / "latents" / f"chunk-{new_chunk:03d}" / camera_dir.name / f"episode_{new_index:06d}_0_{new_length}.pth"
        copy_or_link(src, dst)
        copied += 1
    return copied


def update_sidecar(sidecar, new_index, chunks_size):
    new_chunk = chunk_for_episode(new_index, chunks_size)
    sidecar["episode_index"] = new_index
    video_uris = sidecar.get("video_uris")
    if isinstance(video_uris, dict):
        for key in list(video_uris):
            camera = Path(video_uris[key]).parent.name
            video_uris[key] = f"videos/chunk-{new_chunk:03d}/{camera}/episode_{new_index:06d}.mp4"
    return sidecar


def process_episode_assets(task):
    source = task["source"]
    tmp_output = task["tmp_output"]
    chunks_size = task["chunks_size"]
    old_index = task["old_index"]
    new_index = task["new_index"]
    old_length = task["old_length"]
    global_start = task["global_start"]

    old_chunk = chunk_for_episode(old_index, chunks_size)
    new_chunk = chunk_for_episode(new_index, chunks_size)
    src_data = source / "data" / f"chunk-{old_chunk:03d}" / f"episode_{old_index:06d}.parquet"
    dst_data = tmp_output / "data" / f"chunk-{new_chunk:03d}" / f"episode_{new_index:06d}.parquet"
    dst_data.parent.mkdir(parents=True, exist_ok=True)
    table = remap_table(src_data, new_index, global_start)
    if table.num_rows != old_length:
        raise ValueError(f"episode {old_index} length mismatch: metadata={old_length}, parquet={table.num_rows}")
    pq.write_table(table, dst_data)

    video_count = copy_episode_video_files(source, tmp_output, old_index, new_index, chunks_size)
    latent_count = copy_episode_latent_files(source, tmp_output, old_index, new_index, old_length, old_length, chunks_size)

    sidecar_written = False
    src_sidecar = source / "meta" / "latent_sidecars" / f"episode_{old_index:06d}_latent.json"
    if src_sidecar.exists():
        sidecar = update_sidecar(read_json(src_sidecar), new_index, chunks_size)
        sidecar["original_episode_index"] = old_index
        write_json(tmp_output / "meta" / "latent_sidecars" / f"episode_{new_index:06d}_latent.json", sidecar)
        sidecar_written = True

    emb_copied = False
    uuid = task.get("episode_uuid")
    if uuid:
        src_emb = source / "instruct_emb" / f"{uuid}.pt"
        if src_emb.exists():
            copy_or_link(src_emb, tmp_output / "instruct_emb" / src_emb.name)
            emb_copied = True

    return {
        "new_index": new_index,
        "old_index": old_index,
        "rows": table.num_rows,
        "videos": video_count,
        "latents": latent_count,
        "sidecar": sidecar_written,
        "embedding": emb_copied,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--workers", type=int, default=1, help="parallel episode workers; default keeps historical serial behavior")
    parser.add_argument("--limit-accepted", type=int, default=0, help="test mode: keep only the first N accepted episodes")
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if output.exists():
        if not args.overwrite:
            raise SystemExit(f"Output exists: {output}")
        shutil.rmtree(output)
    tmp_output = output.with_name(output.name + f".tmp.{int(time.time())}")
    if tmp_output.exists():
        shutil.rmtree(tmp_output)

    info = read_json(source / "meta" / "info.json")
    chunks_size = int(info.get("chunks_size") or 1000)
    final_status, labels_by_episode = final_episode_status(args.labels)
    episodes = {int(row["episode_index"]): row for row in read_jsonl(source / "meta" / "episodes.jsonl")}
    stats = {int(row["episode_index"]): row for row in read_jsonl(source / "meta" / "episodes_stats.jsonl")}
    accepted_matches, rejected_matches, match_summary = resolve_episode_matches(episodes, final_status, labels_by_episode)
    accepted_total = len(accepted_matches)
    if args.limit_accepted:
        if args.limit_accepted < 0:
            raise SystemExit("--limit-accepted must be >= 0")
        accepted_matches = accepted_matches[: args.limit_accepted]

    total_frames = sum(int(episodes[match["source_episode_index"]].get("length") or 0) for match in accepted_matches)
    total_videos = len(accepted_matches) * 3
    total_chunks = max(1, math.ceil(len(accepted_matches) / chunks_size))

    for subdir in ["data", "videos", "latents", "meta", "instruct_emb", "episode_uuids"]:
        (tmp_output / subdir).mkdir(parents=True, exist_ok=True)

    for src_file in source.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, tmp_output / src_file.name)

    for meta_name in ["tasks.jsonl", "modality.json"]:
        src = source / "meta" / meta_name
        if src.exists():
            shutil.copy2(src, tmp_output / "meta" / meta_name)

    new_info = dict(info)
    new_info["total_episodes"] = len(accepted_matches)
    new_info["total_frames"] = total_frames
    new_info["total_videos"] = total_videos
    new_info["total_chunks"] = total_chunks
    new_info["splits"] = {"train": f"0:{len(accepted_matches)}"}
    write_json(tmp_output / "meta" / "info.json", new_info)

    new_episodes = []
    new_stats = []
    qc_rows = []
    tasks = []
    global_start = 0
    for new_index, match in enumerate(accepted_matches):
        old_index = match["source_episode_index"]
        old_episode = dict(episodes[old_index])
        old_length = int(old_episode.get("length") or 0)

        old_episode["original_episode_index"] = old_index
        old_episode["episode_index"] = new_index
        new_episodes.append(old_episode)

        if old_index in stats:
            stat_row = dict(stats[old_index])
            stat_row["original_episode_index"] = old_index
            stat_row["episode_index"] = new_index
            new_stats.append(stat_row)

        tasks.append(
            {
                "source": source,
                "tmp_output": tmp_output,
                "chunks_size": chunks_size,
                "old_index": old_index,
                "new_index": new_index,
                "old_length": old_length,
                "global_start": global_start,
                "episode_uuid": old_episode.get("episode_uuid"),
            }
        )
        global_start += old_length

        qc_rows.append(
            {
                "new_episode_index": new_index,
                "original_episode_index": old_index,
                "label_episode_index": match["label_episode_index"],
                "episode_uuid": old_episode.get("episode_uuid"),
                "final_status": "accept",
                "match_mode": match["match_mode"],
                "labels": match["labels"],
            }
        )

    workers = max(1, args.workers)
    if workers == 1 or len(tasks) <= 1:
        for task in tasks:
            process_episode_assets(task)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_episode_assets, task) for task in tasks]
            for future in concurrent.futures.as_completed(futures):
                future.result()

    write_jsonl(tmp_output / "meta" / "episodes.jsonl", new_episodes)
    write_jsonl(tmp_output / "meta" / "episodes_stats.jsonl", new_stats)
    with open(tmp_output / "episode_uuids" / "episode_uuids_part_001.txt", "w", encoding="utf-8") as f:
        for row in new_episodes:
            f.write(str(row.get("episode_uuid", "")))
            f.write("\n")
    write_jsonl(tmp_output / "meta" / "qc_filter_accept_mapping.jsonl", qc_rows)
    write_json(
        tmp_output / "meta" / "qc_filter_summary.json",
        {
            "source": str(source),
            "labels": str(args.labels),
            "output": str(output),
            "accepted": len(accepted_matches),
            "rejected": len(rejected_matches),
            "accepted_total_before_limit": accepted_total,
            "limit_accepted": args.limit_accepted or None,
            "match": match_summary,
            "accepted_original_episode_indices": [match["source_episode_index"] for match in accepted_matches],
            "accepted_label_episode_indices": [match["label_episode_index"] for match in accepted_matches],
            "rejected_original_episode_indices": [match["source_episode_index"] for match in rejected_matches],
            "rejected_label_episode_indices": [match["label_episode_index"] for match in rejected_matches],
        },
    )

    os.rename(tmp_output, output)
    print(
        json.dumps(
            {
                "output": str(output),
                "accepted": len(accepted_matches),
                "accepted_total_before_limit": accepted_total,
                "rejected": len(rejected_matches),
                "frames": total_frames,
                "match_mode": match_summary["match_mode"],
                "index_uuid_mismatch_count": match_summary["index_uuid_mismatch_count"],
                "workers": workers,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
