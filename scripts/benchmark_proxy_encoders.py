#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import time
from pathlib import Path


def video_inputs(dataset: Path, sample_count: int) -> list[Path]:
    videos = sorted(dataset.glob("videos/chunk-*/*/episode_*.mp4"))
    if sample_count <= 0 or len(videos) <= sample_count:
        return videos
    step = len(videos) / sample_count
    return [videos[min(len(videos) - 1, int(index * step))] for index in range(sample_count)]


def query_gpus() -> list[dict[str, float | int]]:
    proc = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    gpus: list[dict[str, float | int]] = []
    for line in (proc.stdout or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            index = int(parts[0])
            util = int(float(parts[1]))
            memory_used = float(parts[2])
            memory_total = float(parts[3]) or 1.0
        except ValueError:
            continue
        gpus.append(
            {
                "index": index,
                "util": util,
                "memory_used": memory_used,
                "memory_total": memory_total,
                "memory_ratio": round(memory_used / memory_total, 4),
            }
        )
    return gpus


def ffmpeg_cmd(src: Path, dst: Path, encoder: str, crf: int, gpu_index: int | None) -> list[str]:
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0", "-an"]
    if encoder == "h264_nvenc":
        cmd = base + ["-c:v", "h264_nvenc", "-preset", "fast", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
        if gpu_index is not None:
            cmd += ["-gpu", str(gpu_index)]
        return cmd + ["-pix_fmt", "yuv420p", "-profile:v", "main", "-level", "3.1", "-movflags", "+faststart", str(dst)]
    return base + [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
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
        str(dst),
    ]


def transcode_one(src: Path, dst: Path, encoder: str, crf: int, gpu_index: int | None) -> dict[str, object]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    proc = subprocess.run(
        ffmpeg_cmd(src, dst, encoder, crf, gpu_index),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        return {"ok": False, "elapsed": elapsed, "bytes": 0, "error": (proc.stderr or "")[-300:]}
    return {"ok": True, "elapsed": elapsed, "bytes": dst.stat().st_size if dst.exists() else 0, "error": ""}


def run_bench(
    name: str,
    sample: list[Path],
    output_root: Path,
    encoder: str,
    workers: int,
    crf: int,
    gpu_indices: list[int],
) -> dict[str, object]:
    output_dir = output_root / name
    started = time.perf_counter()
    ok = 0
    failed = 0
    output_bytes = 0
    errors: list[str] = []
    durations: list[float] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = []
        for index, src in enumerate(sample):
            gpu_index = gpu_indices[index % len(gpu_indices)] if encoder == "h264_nvenc" and gpu_indices else None
            futures.append(pool.submit(transcode_one, src, output_dir / f"{index:06d}.mp4", encoder, crf, gpu_index))
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            durations.append(float(result["elapsed"]))
            if result["ok"]:
                ok += 1
                output_bytes += int(result["bytes"])
            else:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(result["error"]))
    elapsed = time.perf_counter() - started
    return {
        "name": name,
        "encoder": encoder,
        "workers": workers,
        "gpu_indices": gpu_indices,
        "sample_count": len(sample),
        "ok": ok,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 3),
        "videos_per_second": round(ok / elapsed, 3) if elapsed > 0 else None,
        "avg_single_ffmpeg_seconds": round(sum(durations) / len(durations), 3) if durations else None,
        "output_mb": round(output_bytes / 1024 / 1024, 2),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark QC proxy CPU and NVENC transcode speed.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=480)
    parser.add_argument("--cpu-workers", type=int, default=48)
    parser.add_argument(
        "--gpu-workers-per-gpu",
        type=int,
        action="append",
        default=None,
        help="NVENC worker count per selected GPU; may be repeated. Defaults to 1 and 2.",
    )
    parser.add_argument("--gpu-memory-max-ratio", type=float, default=0.8)
    parser.add_argument("--crf", type=int, default=32)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--keep-output", action="store_true")
    args = parser.parse_args()

    dataset = args.dataset.expanduser().resolve()
    sample = video_inputs(dataset, args.sample_count)
    if not sample:
        raise SystemExit(f"No videos found in {dataset}")
    output_root = args.output_root or (Path("/tmp") / f"lqcp_proxy_encoder_bench_{int(time.time())}")
    output_root.mkdir(parents=True, exist_ok=True)
    gpus = query_gpus()
    gpu_indices = [int(gpu["index"]) for gpu in gpus if float(gpu["memory_ratio"]) <= args.gpu_memory_max_ratio]
    print(
        json.dumps(
            {
                "event": "start",
                "dataset": str(dataset),
                "sample_count": len(sample),
                "gpu_snapshot": gpus,
                "gpu_indices": gpu_indices,
                "output_root": str(output_root),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    results = [run_bench("cpu_libx264", sample, output_root, "libx264", args.cpu_workers, args.crf, [])]
    gpu_workers_per_gpu = args.gpu_workers_per_gpu or [1, 2]
    for per_gpu in gpu_workers_per_gpu:
        if gpu_indices:
            workers = max(1, len(gpu_indices) * per_gpu)
            results.append(run_bench(f"nvenc_w{workers}", sample, output_root, "h264_nvenc", workers, args.crf, gpu_indices))
    print(json.dumps({"event": "results", "results": results}, ensure_ascii=False), flush=True)
    if not args.keep_output:
        shutil.rmtree(output_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
