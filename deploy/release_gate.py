#!/usr/bin/env python3
"""Run the reproducible local release checks used before a server rollout."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MIGRATION = "o0d1e2f3g4h5"


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    print(f"\n> {' '.join(command)}", flush=True)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode:
        raise SystemExit(result.returncode)
    return result.stdout


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Release gate requires '{name}' in PATH.")


def verify_clean_tree() -> None:
    output = run(["git", "status", "--porcelain"])
    unexpected = [
        line
        for line in output.splitlines()
        if not line.endswith(" reports/") and not line.startswith("?? reports/")
    ]
    if unexpected:
        raise SystemExit("Working tree is not clean:\n" + "\n".join(unexpected))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args()

    require_tool("git")
    require_tool("node")
    require_tool("docker")

    if not args.skip_tests:
        run([sys.executable, "-m", "pytest", "-q"])

    for script in sorted((ROOT / "web").rglob("*.js")):
        run(["node", "--check", str(script.relative_to(ROOT))])

    heads = run([sys.executable, "-m", "alembic", "heads"])
    if EXPECTED_MIGRATION not in heads:
        raise SystemExit(
            f"Expected Alembic head {EXPECTED_MIGRATION}, got: {heads.strip()}"
        )

    compose_env = os.environ.copy()
    compose_env["POSTGRES_PASSWORD"] = "release-gate-config-only-password"
    run(["docker", "compose", "config", "--quiet"], env=compose_env)
    run(["git", "diff", "--check"])

    if args.require_clean:
        verify_clean_tree()

    print("\nRelease gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
