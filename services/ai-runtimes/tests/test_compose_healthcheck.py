from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "deploy" / "ai-runtimes" / "docker-compose.yml"


def test_compose_healthchecks_enforce_runtime_ready_flag() -> None:
    commands = _healthcheck_commands()

    assert len(commands) == 3
    for command in commands:
        assert "json.loads" in command
        assert ".get('ready'," in command or '.get("ready",' in command
        assert "raise SystemExit" in command or "sys.exit" in command


def _healthcheck_commands() -> list[str]:
    commands: list[str] = []
    for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines():
        if "urllib.request.urlopen" not in line:
            continue
        commands.append(line.strip().strip('"'))
    return commands
