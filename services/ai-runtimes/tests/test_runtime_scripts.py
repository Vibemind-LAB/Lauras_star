from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
POWERSHELL_SCRIPT = REPO_ROOT / "scripts" / "ai-runtimes.ps1"
BASH_SCRIPT = REPO_ROOT / "scripts" / "ai-runtimes.sh"


def test_runtime_health_scripts_fail_when_sidecar_is_not_ready() -> None:
    powershell = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    bash = BASH_SCRIPT.read_text(encoding="utf-8")

    assert "$ready" in powershell
    assert "throw" in powershell
    assert "data.get(\"ready\"" in bash
    assert "raise SystemExit" in bash
