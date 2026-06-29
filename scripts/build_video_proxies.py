#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = Path("/mnt/nm_dataset/dataset/giftbox_0628_1912episodes")
EPISODE_RE = re.compile(r"episode_(\d+)\.mp4$")
CAMERA_ORDER = {
    "observation.images.image": 0,
    "observation.images.wrist_image_1": 1,
    "observation.images.wrist_image_2": 2,
}


def dataset_id(dataset_path: Path) -> str:
    digest = hashlib.sha1(str(dataset_path).encode("utf-8")).hexdigest()[:12]
    basename = re.sub(r"[^A-Za-z0-9_.-]+", "_", dataset_path.name).strip("_") or "dataset"
    return f"{basename}-{digest}"


def video_inputs(dataset_path: Path) -> list[Path]:
    def sort_key(path: Path) -> tuple[int, int, str]:
        match = EPISODE_RE.match(path.name)
        episode = int(match.group(1)) if match else 10**12
        camera = path.parent.name
        return (episode, CAMERA_ORDER.get(camera, 99), camera)

    return sorted((dataset_path / "videos").glob("chunk-*/*/episode_*.mp4"), key=sort_key)


def needs_rebuild(src: Path, dst: Path, overwrite: bool) -> bool:
    if overwrite:
        return True
    if not dst.exists() or dst.stat().st_size == 0:
        return True
    return dst.stat().st_mtime < src.stat().st_mtime


def transcode_one(src: Path, dst: Path, crf: int, preset: str, overwrite: bool) -> tuple[str, str]:
    if not needs_rebuild(src, dst, overwrite):
        return ("skip", str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp.mp4")
    if tmp.exists():
        tmp.unlink()
    cmd = [
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
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
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
    subprocess.run(cmd, check=True)
    os.replace(tmp, dst)
    return ("built", str(dst))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build H.264 faststart proxy videos for the QC platform.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "video_proxy")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--crf", type=int, default=28)
    parser.add_argument("--preset", default="veryfast")
    parser.add_argument("--limit-videos", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    dataset_path = args.dataset.expanduser().resolve()
    output_root = args.output_root.expanduser().resolve() / dataset_id(dataset_path)
    inputs = video_inputs(dataset_path)
    if args.limit_videos > 0:
        inputs = inputs[: args.limit_videos]
    print(f"dataset={dataset_path}")
    print(f"output={output_root}")
    print(f"videos={len(inputs)}")

    built = skipped = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = []
        for src in inputs:
            rel = src.relative_to(dataset_path)
            dst = output_root / rel
            futures.append(pool.submit(transcode_one, src, dst, args.crf, args.preset, args.overwrite))
        for future in concurrent.futures.as_completed(futures):
            try:
                status, path = future.result()
            except Exception as exc:
                failed += 1
                print(f"failed: {exc}", flush=True)
                continue
            if status == "built":
                built += 1
            else:
                skipped += 1
            print(f"{status}: {path}", flush=True)

    print(f"done built={built} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
