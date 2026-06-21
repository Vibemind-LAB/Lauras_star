from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-root", required=True)
    args = parser.parse_args(argv)

    model_root = Path(args.model_root)
    repo = Path(os.environ.get("LAURA_MUSETALK_REPO", str(model_root / "MuseTalk")))
    result_dir = Path(os.environ.get("LAURA_MUSETALK_RESULT_DIR", "/workspace/musetalk-results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    version = os.environ.get("LAURA_MUSETALK_VERSION", "v15")
    weights_root = Path(os.environ.get("LAURA_MUSETALK_WEIGHTS_ROOT", str(repo / "models")))

    with tempfile.TemporaryDirectory(prefix="laura-musetalk-") as tmp:
        config_path = Path(tmp) / "inference.yaml"
        _write_inference_config(config_path, video=Path(args.video), audio=Path(args.audio))
        command = [
            sys.executable,
            "-m",
            "scripts.inference",
            "--inference_config",
            str(config_path),
            "--result_dir",
            str(result_dir),
            "--unet_model_path",
            str(weights_root / "musetalkV15" / "unet.pth"),
            "--unet_config",
            str(weights_root / "musetalkV15" / "musetalk.json"),
            "--version",
            version,
        ]
        ffmpeg_path = os.environ.get("LAURA_MUSETALK_FFMPEG_PATH")
        if ffmpeg_path:
            command.extend(["--ffmpeg_path", ffmpeg_path])
        command.extend(shlex.split(os.environ.get("LAURA_MUSETALK_EXTRA_ARGS", "")))
        subprocess.run(command, cwd=repo, check=True)

    pattern = os.environ.get("LAURA_MUSETALK_OUTPUT_GLOB", "**/*.mp4")
    _copy_newest(result_dir, pattern, Path(args.output))
    return 0


def _write_inference_config(path: Path, *, video: Path, audio: Path) -> None:
    path.write_text(
        "task_0:\n"
        f"  video_path: {video.as_posix()}\n"
        f"  audio_path: {audio.as_posix()}\n",
        encoding="utf-8",
    )


def _copy_newest(root: Path, pattern: str, output: Path) -> None:
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        raise FileNotFoundError(f"no MuseTalk output matched {pattern!r} in {root}")
    newest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(newest, output)


if __name__ == "__main__":
    raise SystemExit(main())
