from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "deploy" / "production-rollout.sh"


def test_rollout_is_pinned_backed_up_and_readiness_gated() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert source.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail")
    assert "git status --porcelain --untracked-files=no" in source
    assert "AAP_RELEASE_COMMIT" in source
    assert "production_preflight.py" in source
    assert "--commercial" in source
    assert "docker compose build" in source
    assert "deploy/backup-data.sh" in source
    assert source.index("docker compose build") < source.index("deploy/backup-data.sh")
    assert source.index("deploy/backup-data.sh") < source.index("docker compose up")
    assert "/api/health/ready" in source
    assert "s4h5i6j7k8l9" in source
    assert "production_smoke.py" in source
    assert "--require-ai" in source


def test_rollout_never_pulls_an_unreviewed_revision() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "git pull" not in source
    assert "git reset" not in source
    assert "docker compose down" not in source
