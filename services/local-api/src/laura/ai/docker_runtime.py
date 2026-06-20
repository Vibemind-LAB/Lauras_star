from __future__ import annotations

import re
import subprocess
from typing import Any

from .runtime_types import RuntimeHealth

_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")


class DockerAdapter:
    """Small Docker CLI adapter. Dependency-free and easy to fake in tests."""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None

    def _missing_cli_health(self) -> RuntimeHealth:
        return RuntimeHealth("error", False, "docker CLI not available")

    def start(self, runtime: dict[str, Any]) -> RuntimeHealth:
        name = str(runtime.get("container_name") or "")
        image = str(runtime.get("container_image") or "")
        port = runtime.get("port")
        if not name or not image:
            return RuntimeHealth("error", False, "container_name and container_image are required")

        existing = self._run(
            ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"]
        )
        if existing is None:
            return self._missing_cli_health()
        if name in existing.stdout.splitlines():
            result = self._run(
                ["docker", "start", name],
            )
        else:
            cmd = ["docker", "run", "-d", "--name", name]
            if port is not None:
                cmd.extend(["-p", f"{int(port)}:{int(port)}"])
            cmd.extend(_container_env_args(runtime, port=port))
            workspace_mount = runtime.get("workspace_mount")
            if isinstance(workspace_mount, str) and workspace_mount:
                cmd.extend(["-v", _normalize_mount(workspace_mount, "/workspace")])
            model_mount = runtime.get("model_mount")
            if isinstance(model_mount, str) and model_mount:
                cmd.extend(["-v", _normalize_mount(model_mount, "/models", read_only=True)])
            if runtime.get("requires_gpu"):
                cmd.extend(["--gpus", "all"])
            cmd.append(image)
            result = self._run(cmd)

        if result is None:
            return self._missing_cli_health()
        if result.returncode != 0:
            return RuntimeHealth("error", False, (result.stderr or result.stdout).strip())
        return RuntimeHealth("starting", False, "container started")

    def stop(self, runtime: dict[str, Any]) -> RuntimeHealth:
        name = str(runtime.get("container_name") or "")
        if not name:
            return RuntimeHealth("error", False, "container_name is required")
        result = self._run(
            ["docker", "stop", name],
        )
        if result is None:
            return self._missing_cli_health()
        if result.returncode != 0:
            return RuntimeHealth("error", False, (result.stderr or result.stdout).strip())
        return RuntimeHealth("stopped", False, "container stopped")

    def logs(self, runtime: dict[str, Any], tail: int = 100) -> str:
        name = str(runtime.get("container_name") or "")
        if not name:
            return ""
        result = self._run(
            ["docker", "logs", "--tail", str(tail), name],
        )
        if result is None:
            return "docker CLI not available"
        return result.stdout if result.returncode == 0 else result.stderr


def _container_env_args(runtime: dict[str, Any], *, port: object) -> list[str]:
    raw_env = runtime.get("container_env")
    env: dict[str, str] = {}
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            if isinstance(key, str) and isinstance(value, str) and _ENV_NAME_RE.fullmatch(key):
                env[key] = value
    if isinstance(port, (int, str)) and "LAURA_RUNTIME_PORT" not in env:
        env["LAURA_RUNTIME_PORT"] = str(int(port))

    args: list[str] = []
    for key in sorted(env):
        args.extend(["-e", f"{key}={env[key]}"])
    return args


def _normalize_mount(raw_mount: str, target: str, *, read_only: bool = False) -> str:
    mount = raw_mount.strip()
    if _has_container_target(mount):
        return mount
    suffix = ":ro" if read_only else ""
    return f"{mount}:{target}{suffix}"


def _has_container_target(mount: str) -> bool:
    if _WINDOWS_ABS_RE.match(mount):
        return ":" in mount[2:]
    return ":" in mount
