from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "services" / "ai-runtimes" / "Dockerfile.musetalk"
MODELS_COMPOSE = REPO_ROOT / "deploy" / "ai-runtimes" / "docker-compose.models.yml"


def test_musetalk_image_loads_unet_checkpoint_on_cpu_before_gpu_transfer() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "map_location='cpu'" in dockerfile
    assert "/opt/MuseTalk/musetalk/models/unet.py" in dockerfile


def test_musetalk_model_mode_uses_patched_code_with_mounted_weights() -> None:
    compose = MODELS_COMPOSE.read_text(encoding="utf-8")

    assert "LAURA_MUSETALK_REPO: /opt/MuseTalk" in compose
    assert "LAURA_MUSETALK_WEIGHTS_ROOT: /models/MuseTalk/models" in compose


def test_musetalk_image_links_repo_models_before_switching_to_runtime_user() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    link_command = "ln -s /models/MuseTalk/models /opt/MuseTalk/models"

    assert link_command in dockerfile
    assert dockerfile.index(link_command) < dockerfile.index("USER laura")


def test_musetalk_image_hardens_inference_ffmpeg_finalization() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "LAURA_MUSETALK_FFMPEG_TIMEOUT" in dockerfile
    assert "subprocess.DEVNULL" in dockerfile
    assert "subprocess.run(" in dockerfile
    assert "check=True" in dockerfile
    assert "timeout=timeout" in dockerfile
    assert "ffmpeg -nostdin" in dockerfile
    assert "os.system(cmd_img2video)" in dockerfile
    assert "_run_ffmpeg_command(cmd_img2video)" in dockerfile
    assert "os.system(cmd_combine_audio)" in dockerfile
    assert "_run_ffmpeg_command(cmd_combine_audio)" in dockerfile
