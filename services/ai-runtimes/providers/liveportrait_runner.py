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
    parser.add_argument("--portrait", required=True)
    parser.add_argument("--driving", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-root", required=True)
    args = parser.parse_args(argv)

    model_root = Path(args.model_root)
    repo = Path(os.environ.get("LAURA_LIVEPORTRAIT_REPO", str(model_root / "LivePortrait")))
    output = Path(args.output)
    with tempfile.TemporaryDirectory(prefix="laura-liveportrait-out-") as tmp:
        output_dir = Path(tmp) / "animations"
        command = [
            sys.executable,
            "inference.py",
            "-s",
            str(Path(args.portrait)),
            "-d",
            str(Path(args.driving)),
            "-o",
            str(output_dir),
        ]
        command.extend(shlex.split(os.environ.get("LAURA_LIVEPORTRAIT_EXTRA_ARGS", "")))
        subprocess.run(command, cwd=repo, check=True)

        pattern = os.environ.get("LAURA_LIVEPORTRAIT_OUTPUT_GLOB", "*.mp4")
        _copy_newest(output_dir, pattern, output)
    return 0


def _copy_newest(root: Path, pattern: str, output: Path) -> None:
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        raise FileNotFoundError(f"no LivePortrait output matched {pattern!r} in {root}")
    newest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(newest, output)


if __name__ == "__main__":
    raise SystemExit(main())
