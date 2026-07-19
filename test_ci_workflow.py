from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github" / "workflows" / "release-gate.yml"


def workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_ci_runs_complete_gate_with_read_only_permissions() -> None:
    config = workflow()

    assert config["permissions"] == {"contents": "read"}
    assert set(config["on"]) == {"push", "pull_request", "workflow_dispatch"}
    quality = config["jobs"]["quality"]
    steps = quality["steps"]
    uses = [step.get("uses") for step in steps if step.get("uses")]
    runs = [step.get("run", "") for step in steps]

    assert "actions/checkout@v7" in uses
    assert "actions/setup-python@v6" in uses
    assert "actions/setup-node@v6" in uses
    assert any("requirements-dev.txt" in command for command in runs)
    assert any("release_gate.py --require-clean" in command for command in runs)


def test_ci_builds_and_executes_the_production_image_without_publishing() -> None:
    config = workflow()
    docker_job = config["jobs"]["docker-image"]

    assert docker_job["needs"] == "quality"
    steps = docker_job["steps"]
    uses = [step.get("uses") for step in steps if step.get("uses")]
    build = next(step for step in steps if step.get("uses") == "docker/build-push-action@v7")
    commands = "\n".join(step.get("run", "") for step in steps)

    assert "docker/setup-buildx-action@v4" in uses
    assert build["with"]["load"] == "true"
    assert build["with"]["push"] == "false"
    assert build["with"]["tags"] == "allasplanned:ci"
    assert "--network none" in commands
    assert "import server" in commands
    assert "ffmpeg -version" in commands
    assert "node --version" in commands
