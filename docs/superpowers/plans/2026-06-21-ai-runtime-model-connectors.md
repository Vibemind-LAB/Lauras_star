# AI Runtime Model Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die vorhandenen Laura-Sidecars von reinem Smoke-Betrieb auf echte, lokal installierbare Modell-Connectoren vorbereiten: LivePortrait fuer Reenact, MuseTalk/VibeVideo fuer Lipsync und Piper fuer Voice.

**Architecture:** Laura bleibt model-free; die schweren Repos/Gewichte liegen unter `workspace/models/<runtime>` oder einem externen Mount und werden nur im Sidecar verwendet. Der gemeinsame stdlib-HTTP-Server ruft kleine Provider-Runner auf, die fremde CLIs in Lauras stabile Ein-/Ausgabe-Vertraege uebersetzen. `model` mode wird nur ready, wenn Command und Model-Root konsistent sind; `smoke` mode bleibt der schnelle Contract-Test.

**Tech Stack:** Python-stdlib im Sidecar, Docker/Compose, PowerShell/Bash helper scripts, pytest, ruff, mypy. Externe Modellquellen bleiben optional: KlingAIResearch/LivePortrait, TMElyralab/MuseTalk, OHF-Voice/Piper.

## Global Constraints

- Keine Modellgewichte, gated Repositories, Tokens oder Lizenzartefakte in Git.
- Laura Core importiert weiterhin keine Torch/CUDA/LivePortrait/MuseTalk/Piper-Libs.
- Backend und Desktop muessen ohne Docker, GPU und Modelle starten.
- `smoke` mode bleibt deterministisch und modellfrei.
- `model` mode liefert klare Health-/Fehlermeldungen statt stiller Fallbacks.
- Sidecar-Outputs gehen weiter durch bestehende Sync-/Provenance-/Consent-Gates im Laura-Core.
- Code-Identifier und Kommentare Englisch; Doku-Prosa Deutsch.

---

## File Structure

- Modify `services/ai-runtimes/runtime_server.py`
  - Add optional `LAURA_VIBEVIDEO_PROBE_COMMAND` support.
  - Add `provider` and command readiness details to `/healthz` and `/capabilities`.
  - Keep existing generic `{placeholder}` command execution.
- Add `services/ai-runtimes/providers/liveportrait_runner.py`
  - Bridges LivePortrait `inference.py -s <portrait> -d <driving>` to Laura's required explicit `{output}` file.
- Add `services/ai-runtimes/providers/musetalk_runner.py`
  - Writes a temporary MuseTalk inference YAML from `{video}` and `{audio}`, invokes `python -m scripts.inference`, and copies the newest MP4 into `{output}`.
- Add `services/ai-runtimes/providers/piper_voice_runner.py`
  - Reads Laura's `request.json`, extracts text, calls `python -m piper -m <voice> -f <output> -- <text>`.
- Add `services/ai-runtimes/providers/__init__.py`
  - Package marker for tests and module invocation.
- Add `services/ai-runtimes/tests/test_provider_runners.py`
  - Unit tests for command construction and output-copy behavior without real model deps.
- Modify `services/ai-runtimes/tests/test_runtime_server.py`
  - Covers model-mode probe command and health/capabilities diagnostics.
- Modify `deploy/ai-runtimes/docker-compose.yml`
  - Add commented/empty model-command env vars and `LAURA_RUNTIME_PROVIDER`.
- Modify `scripts/register-ai-sidecars.ps1`
  - Register model-mode command envs when requested.
- Modify `services/ai-runtimes/README.md`
  - Document real connector layout, commands, install references and license boundaries.
- Modify `tasks/todo.md`
  - Add APF3 status after verification.

---

### Task 1: Runtime Diagnostics and Probe Command

**Files:**
- Modify: `services/ai-runtimes/runtime_server.py`
- Modify: `services/ai-runtimes/tests/test_runtime_server.py`

**Interfaces:**
- New env: `LAURA_VIBEVIDEO_PROBE_COMMAND`
- `RuntimeConfig.probe_command: str | None`
- `GET /healthz` includes `provider`, `command_configured`, `model_root_exists`.
- `GET /capabilities` includes `provider`, `command_env`, `probe_command_configured`.
- `POST /probe` in model mode runs the configured probe command when present and returns its JSON output.

- [ ] **Step 1: Write RED tests**

Add two tests:

```python
def test_model_health_exposes_command_and_model_root_diagnostics(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    config = RuntimeConfig(
        kind="voice",
        mode="model",
        port=0,
        model_root=missing_root,
        command="python -m providers.piper_voice_runner --request {request_json} --output {output}",
        provider="piper",
    )
    with running_runtime(config) as port:
        health = _json_request(port, "GET", "/healthz")
        caps = _json_request(port, "GET", "/capabilities")

    assert health["ready"] is False
    assert health["provider"] == "piper"
    assert health["command_configured"] is True
    assert health["model_root_exists"] is False
    assert caps["provider"] == "piper"
    assert "LAURA_VOICE_COMMAND" in caps["command_env"]
```

```python
def test_vibevideo_model_probe_uses_probe_command(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[-1]).write_text(json.dumps({"
        "'face_detected': True, 'mouth_visible': False, 'audio_present': True"
        "}))\n",
        encoding="utf-8",
    )
    output = tmp_path / "probe.json"
    config = RuntimeConfig(
        kind="vibevideo",
        mode="model",
        port=0,
        model_root=tmp_path,
        command="python -c \"from pathlib import Path; Path({output!r}).write_bytes(Path({video!r}).read_bytes())\"",
        probe_command=f"{sys.executable} {probe} --video {{video}} --audio {{audio}} --output {{probe_json}}",
    )
    multipart, content_type = _multipart(
        files={
            "video": ("v.mp4", b"video", "video/mp4"),
            "audio": ("a.wav", b"audio", "audio/wav"),
        }
    )
    with running_runtime(config) as port:
        result = _json_request(port, "POST", "/probe", body=multipart, headers={"Content-Type": content_type})

    assert result == {"face_detected": True, "mouth_visible": False, "audio_present": True}
```

- [ ] **Step 2: Run RED**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_runtime_server.py -q
```

Expected: FAIL because `provider`, `probe_command`, and diagnostic keys do not exist.

- [ ] **Step 3: Implement minimal diagnostics and probe command**

Add fields to `RuntimeConfig`, read `LAURA_RUNTIME_PROVIDER` and `LAURA_VIBEVIDEO_PROBE_COMMAND` in `from_env`, enrich payloads, and run the probe command into `probe.json` when configured.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_runtime_server.py -q
uv run ruff check ..\ai-runtimes\runtime_server.py ..\ai-runtimes\tests\test_runtime_server.py
uv run mypy ..\ai-runtimes\runtime_server.py
```

Expected: PASS.

### Task 2: LivePortrait Provider Runner

**Files:**
- Add: `services/ai-runtimes/providers/__init__.py`
- Add: `services/ai-runtimes/providers/liveportrait_runner.py`
- Modify: `services/ai-runtimes/tests/test_provider_runners.py`

**Interfaces:**
- CLI:

```powershell
python -m providers.liveportrait_runner --portrait <path> --driving <path> --output <path> --model-root /models
```

- Env:
  - `LAURA_LIVEPORTRAIT_REPO=/models/LivePortrait`
  - `LAURA_LIVEPORTRAIT_EXTRA_ARGS=--flag_crop_driving_video`
  - `LAURA_LIVEPORTRAIT_OUTPUT_GLOB=animations/*.mp4`

- [ ] **Step 1: Write RED tests**

Create tests asserting the runner calls `inference.py -s <portrait> -d <driving>` in the configured repo, appends extra args with `shlex.split`, and copies the newest MP4 matching the output glob to Laura's explicit output path.

- [ ] **Step 2: Run RED**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_provider_runners.py -q
```

Expected: FAIL because provider package does not exist.

- [ ] **Step 3: Implement runner**

Implement with `argparse`, `os.environ`, `subprocess.run`, `Path.glob`, `shutil.copyfile`, no heavy imports and no `print`.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_provider_runners.py -q
uv run ruff check ..\ai-runtimes\providers ..\ai-runtimes\tests\test_provider_runners.py
uv run mypy ..\ai-runtimes\providers
```

Expected: PASS.

### Task 3: MuseTalk/VibeVideo Provider Runner

**Files:**
- Add: `services/ai-runtimes/providers/musetalk_runner.py`
- Modify: `services/ai-runtimes/tests/test_provider_runners.py`

**Interfaces:**
- CLI:

```powershell
python -m providers.musetalk_runner --video <path> --audio <path> --output <path> --model-root /models
```

- Env:
  - `LAURA_MUSETALK_REPO=/models/MuseTalk`
  - `LAURA_MUSETALK_VERSION=v15`
  - `LAURA_MUSETALK_RESULT_DIR=/workspace/musetalk-results`
  - `LAURA_MUSETALK_OUTPUT_GLOB=**/*.mp4`
  - `LAURA_MUSETALK_EXTRA_ARGS=--skip_save_images`

- [ ] **Step 1: Write RED tests**

Extend provider tests to assert:
- the runner writes YAML containing `video_path` and `audio_path`;
- the subprocess command includes `python -m scripts.inference`;
- the newest MP4 from the result directory is copied to `--output`.

- [ ] **Step 2: Run RED**

Run provider tests and watch the MuseTalk test fail with missing module.

- [ ] **Step 3: Implement runner**

Implement YAML writing with explicit string escaping for paths, call MuseTalk from repo cwd, and copy newest MP4.

- [ ] **Step 4: Run GREEN**

Run provider tests, ruff and mypy for `services/ai-runtimes/providers`.

### Task 4: Piper Voice Provider Runner

**Files:**
- Add: `services/ai-runtimes/providers/piper_voice_runner.py`
- Modify: `services/ai-runtimes/tests/test_provider_runners.py`

**Interfaces:**
- CLI:

```powershell
python -m providers.piper_voice_runner --request <request.json> --output <voice.wav> --model-root /models
```

- Env:
  - `LAURA_PIPER_VOICE=en_US-lessac-medium`
  - `LAURA_PIPER_DATA_DIR=/models/piper`
  - `LAURA_PIPER_EXTRA_ARGS=--sentence-silence 0.05`

- [ ] **Step 1: Write RED tests**

Add tests asserting the runner reads `text` from the request JSON, rejects missing text, and calls `python -m piper -m <voice> -f <output> -- <text>` with optional `--data-dir`.

- [ ] **Step 2: Run RED**

Run provider tests and watch the Piper test fail with missing module.

- [ ] **Step 3: Implement runner**

Implement with `argparse`, `json`, `subprocess.run`, `shlex.split`, no model imports.

- [ ] **Step 4: Run GREEN**

Run provider tests, ruff and mypy for provider package.

### Task 5: Compose, Registration, Docs, Verification

**Files:**
- Modify: `deploy/ai-runtimes/docker-compose.yml`
- Modify: `scripts/register-ai-sidecars.ps1`
- Modify: `services/ai-runtimes/README.md`
- Modify: `tasks/todo.md`

**Interfaces:**
- Compose model envs:
  - Voice: `LAURA_VOICE_COMMAND`, `LAURA_RUNTIME_PROVIDER=piper`
  - LivePortrait: `LAURA_LIVEPORTRAIT_COMMAND`, `LAURA_RUNTIME_PROVIDER=liveportrait`
  - VibeVideo: `LAURA_VIBEVIDEO_COMMAND`, `LAURA_VIBEVIDEO_PROBE_COMMAND`, `LAURA_RUNTIME_PROVIDER=musetalk`
- Registration script accepts `-VoiceCommand`, `-LivePortraitCommand`, `-VibeVideoCommand`, `-VibeVideoProbeCommand`.

- [ ] **Step 1: Update Compose and registration**

Set command envs via `${...:-}` so smoke remains default and model mode can be enabled by environment or registration arguments.

- [ ] **Step 2: Update README**

Document the exact provider commands:

```text
LAURA_VOICE_COMMAND="python -m providers.piper_voice_runner --request {request_json} --output {output} --model-root {model_root}"
LAURA_LIVEPORTRAIT_COMMAND="python -m providers.liveportrait_runner --portrait {portrait} --driving {driving} --output {output} --model-root {model_root}"
LAURA_VIBEVIDEO_COMMAND="python -m providers.musetalk_runner --video {video} --audio {audio} --output {output} --model-root {model_root}"
```

- [ ] **Step 3: Update todo**

Add APF3 with exact verification evidence and remaining real-weight caveat.

- [ ] **Step 4: Full verification**

Run:

```powershell
cd services/local-api
uv run pytest ..\ai-runtimes\tests\test_runtime_server.py ..\ai-runtimes\tests\test_provider_runners.py -q
uv run ruff check ..\ai-runtimes\runtime_server.py ..\ai-runtimes\providers ..\ai-runtimes\tests
uv run mypy ..\ai-runtimes\runtime_server.py ..\ai-runtimes\providers
[scriptblock]::Create((Get-Content ..\..\scripts\register-ai-sidecars.ps1 -Raw)) | Out-Null
docker compose -f ..\..\deploy\ai-runtimes\docker-compose.yml config --quiet
```

If Docker Desktop hangs again, record the exact command and use `config --quiet` plus unit tests as the gate for code correctness.

- [ ] **Step 5: Commit**

Run:

```powershell
git add docs/superpowers/plans/2026-06-21-ai-runtime-model-connectors.md services/ai-runtimes deploy/ai-runtimes scripts/register-ai-sidecars.ps1 tasks/todo.md
git commit -m "feat: add AI runtime model connectors"
```
