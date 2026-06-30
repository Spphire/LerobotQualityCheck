#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import shutil
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


def final_episode_status(labels_path):
    store = read_json(labels_path)
    by_episode = {}
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
                    "updated_at": label.get("updated_at"),
                }
            )
    final = {}
    for episode_index, labels in by_episode.items():
        statuses = {label["status"] for label in labels}
        final[episode_index] = "reject" if "reject" in statuses else "accept"
    return final, by_episode


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
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
    accepted_old = sorted(index for index, status in final_status.items() if status == "accept")
    rejected_old = sorted(index for index, status in final_status.items() if status == "reject")
    old_to_new = {old: new for new, old in enumerate(accepted_old)}

    episodes = {int(row["episode_index"]): row for row in read_jsonl(source / "meta" / "episodes.jsonl")}
    stats = {int(row["episode_index"]): row for row in read_jsonl(source / "meta" / "episodes_stats.jsonl")}
    total_frames = sum(int(episodes[old].get("length") or 0) for old in accepted_old)
    total_videos = len(accepted_old) * 3
    total_chunks = max(1, math.ceil(len(accepted_old) / chunks_size))

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
    new_info["total_episodes"] = len(accepted_old)
    new_info["total_frames"] = total_frames
    new_info["total_videos"] = total_videos
    new_info["total_chunks"] = total_chunks
    new_info["splits"] = {"train": f"0:{len(accepted_old)}"}
    write_json(tmp_output / "meta" / "info.json", new_info)

    new_episodes = []
    new_stats = []
    qc_rows = []
    global_start = 0
    for new_index, old_index in enumerate(accepted_old):
        old_episode = dict(episodes[old_index])
        old_length = int(old_episode.get("length") or 0)
        new_chunk = chunk_for_episode(new_index, chunks_size)
        old_chunk = chunk_for_episode(old_index, chunks_size)

        old_episode["original_episode_index"] = old_index
        old_episode["episode_index"] = new_index
        new_episodes.append(old_episode)

        if old_index in stats:
            stat_row = dict(stats[old_index])
            stat_row["original_episode_index"] = old_index
            stat_row["episode_index"] = new_index
            new_stats.append(stat_row)

        src_data = source / "data" / f"chunk-{old_chunk:03d}" / f"episode_{old_index:06d}.parquet"
        dst_data = tmp_output / "data" / f"chunk-{new_chunk:03d}" / f"episode_{new_index:06d}.parquet"
        dst_data.parent.mkdir(parents=True, exist_ok=True)
        table = remap_table(src_data, new_index, global_start)
        pq.write_table(table, dst_data)
        global_start += table.num_rows

        copy_episode_video_files(source, tmp_output, old_index, new_index, chunks_size)
        copy_episode_latent_files(source, tmp_output, old_index, new_index, old_length, old_length, chunks_size)

        src_sidecar = source / "meta" / "latent_sidecars" / f"episode_{old_index:06d}_latent.json"
        if src_sidecar.exists():
            sidecar = update_sidecar(read_json(src_sidecar), new_index, chunks_size)
            sidecar["original_episode_index"] = old_index
            write_json(tmp_output / "meta" / "latent_sidecars" / f"episode_{new_index:06d}_latent.json", sidecar)

        uuid = old_episode.get("episode_uuid")
        if uuid:
            src_emb = source / "instruct_emb" / f"{uuid}.pt"
            if src_emb.exists():
                copy_or_link(src_emb, tmp_output / "instruct_emb" / src_emb.name)

        qc_rows.append(
            {
                "new_episode_index": new_index,
                "original_episode_index": old_index,
                "episode_uuid": old_episode.get("episode_uuid"),
                "final_status": "accept",
                "labels": labels_by_episode.get(old_index, []),
            }
        )

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
            "accepted": len(accepted_old),
            "rejected": len(rejected_old),
            "accepted_original_episode_indices": accepted_old,
            "rejected_original_episode_indices": rejected_old,
        },
    )

    os.rename(tmp_output, output)
    print(json.dumps({"output": str(output), "accepted": len(accepted_old), "rejected": len(rejected_old), "frames": total_frames}, ensure_ascii=False))


if __name__ == "__main__":
    main()
