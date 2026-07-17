import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "deploy" / "load_profile.py"


def load_module():
    spec = importlib.util.spec_from_file_location("aap_load_profile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_percentile_and_summary_are_deterministic() -> None:
    module = load_module()
    samples = [
        module.Sample("health", 200, .1),
        module.Sample("health", 200, .2),
        module.Sample("health", 503, .5, error="degraded"),
    ]

    result = module.summarize(samples, 1)

    assert result["requests"] == 3
    assert result["successful"] == 2
    assert result["failed"] == 1
    assert result["latency_seconds"]["p50"] == .2
    assert result["latency_seconds"]["p95"] == .47
    assert result["errors"][0]["status_code"] == 503


def test_queue_scenario_requires_explicit_billable_confirmation(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setattr(
        "sys.argv",
        ["load_profile.py", "--scenario", "queue"],
    )

    try:
        module.main()
    except SystemExit as exc:
        assert "--confirm-billable" in str(exc)
    else:
        raise AssertionError("The billable queue scenario started without confirmation")


def test_failed_health_samples_produce_a_nonzero_exit(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setattr(
        "sys.argv",
        ["load_profile.py", "--scenario", "health"],
    )
    monkeypatch.setattr(
        module.LoadProfile,
        "health",
        lambda *_args: ([module.Sample("health", 503, .1, error="degraded")], .1),
    )

    assert module.main() == 1
