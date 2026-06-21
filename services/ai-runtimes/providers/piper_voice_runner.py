from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-root", required=True)
    args = parser.parse_args(argv)

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request JSON must be an object")
    text = request.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("request text must be a non-empty string")

    voice = os.environ.get("LAURA_PIPER_VOICE", "en_US-lessac-medium")
    data_dir = os.environ.get("LAURA_PIPER_DATA_DIR")
    command = [
        sys.executable,
        "-m",
        "piper",
        "-m",
        voice,
        "-f",
        str(Path(args.output)),
    ]
    if data_dir:
        command.extend(["--data-dir", data_dir])
    command.extend(shlex.split(os.environ.get("LAURA_PIPER_EXTRA_ARGS", "")))
    command.extend(["--", text])
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
