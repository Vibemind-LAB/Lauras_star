from __future__ import annotations

import subprocess
from typing import Any

from .runtime_types import RuntimeHealth


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
            workspace_mount = runtime.get("workspace_mount")
            if isinstance(workspace_mount, str) and workspace_mount:
                cmd.extend(["-v", workspace_mount])
            model_mount = runtime.get("model_mount")
            if isinstance(model_mount, str) and model_mount:
                cmd.extend(["-v", model_mount])
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
