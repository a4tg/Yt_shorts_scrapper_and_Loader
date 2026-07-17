#!/usr/bin/env python3
"""Reproducible, bounded load scenarios for an All As Planned deployment."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx


@dataclass
class Sample:
    operation: str
    status_code: int
    seconds: float
    bytes_sent: int = 0
    error: str | None = None


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summarize(samples: list[Sample], elapsed: float) -> dict[str, object]:
    latencies = [sample.seconds for sample in samples]
    successes = [sample for sample in samples if 200 <= sample.status_code < 400 and not sample.error]
    return {
        "requests": len(samples),
        "successful": len(successes),
        "failed": len(samples) - len(successes),
        "success_rate": round(len(successes) / max(1, len(samples)), 4),
        "elapsed_seconds": round(elapsed, 4),
        "requests_per_second": round(len(samples) / max(elapsed, 0.0001), 3),
        "latency_seconds": {
            "min": round(min(latencies, default=0), 4),
            "mean": round(statistics.fmean(latencies), 4) if latencies else 0,
            "p50": round(percentile(latencies, .50), 4),
            "p95": round(percentile(latencies, .95), 4),
            "p99": round(percentile(latencies, .99), 4),
            "max": round(max(latencies, default=0), 4),
        },
        "bytes_sent": sum(sample.bytes_sent for sample in samples),
        "errors": [
            {"operation": sample.operation, "status_code": sample.status_code, "error": sample.error}
            for sample in samples if sample.error or not 200 <= sample.status_code < 400
        ][:20],
    }


class LoadProfile:
    def __init__(self, base_url: str, email: str | None, password: str | None, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.timeout = timeout
        self.cookies: dict[str, str] = {}
        self.csrf = ""
        self.workspace_id: str | None = None
        self.project_id: str | None = None

    def client(self, *, authenticated: bool = False) -> httpx.Client:
        client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=True,
            headers={"Origin": self.base_url, "User-Agent": "AAP-load-profile/1.0"},
        )
        if authenticated:
            client.cookies.update(self.cookies)
            client.headers["X-CSRF-Token"] = self.csrf
        return client

    def login(self) -> None:
        if not self.email or not self.password:
            raise SystemExit("Set AAP_LOAD_EMAIL and AAP_LOAD_PASSWORD for this scenario.")
        with self.client() as client:
            response = client.post(
                "/api/auth/login",
                json={"email": self.email.strip().lower(), "password": self.password},
            )
            response.raise_for_status()
            self.cookies = dict(client.cookies)
            self.csrf = client.cookies.get("yt_loader_csrf") or ""
            if not self.csrf:
                raise RuntimeError("The login response did not set the CSRF cookie.")
            workspace_response = client.get("/api/workspaces")
            workspace_response.raise_for_status()
            workspaces = workspace_response.json()
            if not workspaces:
                raise RuntimeError("The test account has no workspace.")
            self.workspace_id = workspaces[0]["id"]
            project_response = client.get(f"/api/workspaces/{self.workspace_id}/projects")
            project_response.raise_for_status()
            projects = project_response.json()
            if not projects:
                raise RuntimeError("The test account has no project.")
            self.project_id = projects[0]["id"]

    @staticmethod
    def measured(operation: str, action: Callable[[], httpx.Response], *, bytes_sent: int = 0) -> Sample:
        started = time.perf_counter()
        try:
            response = action()
            elapsed = time.perf_counter() - started
            error = None if 200 <= response.status_code < 400 else response.text[:500]
            return Sample(operation, response.status_code, elapsed, bytes_sent, error)
        except Exception as exc:
            return Sample(operation, 0, time.perf_counter() - started, bytes_sent, str(exc)[:500])

    def concurrent(
        self,
        users: int,
        iterations: int,
        worker: Callable[[int, int], list[Sample]],
    ) -> tuple[list[Sample], float]:
        started = time.perf_counter()
        samples: list[Sample] = []
        with ThreadPoolExecutor(max_workers=users) as executor:
            futures = [executor.submit(worker, user, iterations) for user in range(users)]
            for future in as_completed(futures):
                samples.extend(future.result())
        return samples, time.perf_counter() - started

    def health(self, users: int, iterations: int) -> tuple[list[Sample], float]:
        def worker(_user: int, count: int) -> list[Sample]:
            with self.client() as client:
                return [
                    self.measured("health.ready", lambda: client.get("/api/health/ready"))
                    for _ in range(count)
                ]

        return self.concurrent(users, iterations, worker)

    def api(self, users: int, iterations: int) -> tuple[list[Sample], float]:
        self.login()
        endpoints = (
            "/api/workspaces",
            f"/api/workspaces/{self.workspace_id}/projects",
            f"/api/projects/{self.project_id}/library",
            f"/api/projects/{self.project_id}/content",
            f"/api/projects/{self.project_id}/attention",
            f"/api/projects/{self.project_id}/graph",
        )

        def worker(_user: int, count: int) -> list[Sample]:
            values: list[Sample] = []
            with self.client(authenticated=True) as client:
                for _ in range(count):
                    for endpoint in endpoints:
                        values.append(self.measured(f"GET {endpoint}", lambda url=endpoint: client.get(url)))
            return values

        return self.concurrent(users, iterations, worker)

    def upload(self, users: int, iterations: int, size_mb: int) -> tuple[list[Sample], float]:
        self.login()
        size = size_mb * 1024 * 1024
        with tempfile.NamedTemporaryFile(prefix="aap-load-", suffix=".pdf", delete=False) as temporary:
            temporary.write(b"%PDF-1.7\n")
            temporary.truncate(size)
            path = Path(temporary.name)

        def worker(user: int, count: int) -> list[Sample]:
            values: list[Sample] = []
            with self.client(authenticated=True) as client:
                for iteration in range(count):
                    name = f"load-{size_mb}mb-{user}-{iteration}.pdf"
                    with path.open("rb") as source:
                        response: httpx.Response | None = None

                        def upload_file() -> httpx.Response:
                            nonlocal response
                            response = client.post(
                                f"/api/projects/{self.project_id}/files",
                                files={"file": (name, source, "application/pdf")},
                            )
                            return response

                        sample = self.measured(
                            "project.file.upload",
                            upload_file,
                            bytes_sent=size,
                        )
                    values.append(sample)
                    if response is not None and 200 <= sample.status_code < 400:
                        uploaded = response.json()
                        if uploaded.get("id"):
                            values.append(self.measured(
                                "project.file.delete",
                                lambda item_id=uploaded["id"]: client.delete(
                                    f"/api/content-attachments/{item_id}"
                                ),
                            ))
            return values

        try:
            return self.concurrent(users, iterations, worker)
        finally:
            path.unlink(missing_ok=True)

    def queue(self, url_file: Path, wait: bool, queue_timeout: float) -> dict[str, object]:
        self.login()
        urls = [
            line.strip() for line in url_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not 1 <= len(urls) <= 20:
            raise SystemExit("The queue URL file must contain between 1 and 20 URLs.")
        payload = {
            "items": [
                {"url": url, "project_id": self.project_id, "logo_tokens": []}
                for url in urls
            ]
        }
        with self.client(authenticated=True) as client:
            started = time.perf_counter()
            response = client.post("/api/videos/download/batch", json=payload)
            response.raise_for_status()
            batch = response.json()
            jobs = batch["jobs"]
            if wait:
                pending = {job["id"] for job in jobs}
                deadline = time.monotonic() + queue_timeout
                while pending:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Queue did not finish within {queue_timeout:.0f} seconds; "
                            f"{len(pending)} jobs are still pending."
                        )
                    statuses = client.post("/api/jobs/statuses", json={"ids": sorted(pending)})
                    statuses.raise_for_status()
                    for item in statuses.json():
                        if item["status"] in {"done", "error", "deleted"}:
                            pending.discard(item["id"])
                    if pending:
                        time.sleep(2)
                jobs = client.post(
                    "/api/jobs/statuses",
                    json={"ids": [job["id"] for job in jobs]},
                ).json()
            return {
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "created_count": batch["created_count"],
                "duplicate_count": batch["duplicate_count"],
                "credits_reserved": batch["credits_reserved"],
                "jobs": jobs,
            }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--base-url", default=os.getenv("AAP_LOAD_BASE_URL", "https://allasplanned.ru"))
    value.add_argument("--scenario", choices=("health", "api", "upload", "queue"), required=True)
    value.add_argument("--users", type=int, default=1)
    value.add_argument("--iterations", type=int, default=5)
    value.add_argument("--size-mb", type=int, default=100)
    value.add_argument("--timeout", type=float, default=120)
    value.add_argument("--queue-timeout", type=float, default=7200)
    value.add_argument("--url-file", type=Path)
    value.add_argument("--wait", action="store_true")
    value.add_argument("--confirm-billable", action="store_true")
    value.add_argument("--output", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    if not 1 <= args.users <= 25 or not 1 <= args.iterations <= 100:
        raise SystemExit("users must be 1..25 and iterations must be 1..100")
    if not 1 <= args.size_mb <= 1024:
        raise SystemExit("size-mb must be 1..1024")
    if args.timeout <= 0 or args.queue_timeout <= 0:
        raise SystemExit("timeouts must be greater than zero")
    profile = LoadProfile(
        args.base_url,
        os.getenv("AAP_LOAD_EMAIL"),
        os.getenv("AAP_LOAD_PASSWORD"),
        args.timeout,
    )
    started_at = datetime.now(timezone.utc).isoformat()
    if args.scenario == "queue":
        if not args.confirm_billable:
            raise SystemExit("Queue processing is billable. Repeat with --confirm-billable.")
        if not args.url_file:
            raise SystemExit("--url-file is required for the queue scenario.")
        result: dict[str, object] = profile.queue(
            args.url_file,
            args.wait,
            args.queue_timeout,
        )
    else:
        if args.scenario == "health":
            samples, elapsed = profile.health(args.users, args.iterations)
        elif args.scenario == "api":
            samples, elapsed = profile.api(args.users, args.iterations)
        else:
            samples, elapsed = profile.upload(args.users, args.iterations, args.size_mb)
        result = {
            "summary": summarize(samples, elapsed),
            "samples": [asdict(sample) for sample in samples],
        }
    report = {
        "started_at": started_at,
        "base_url": args.base_url,
        "scenario": args.scenario,
        "users": args.users,
        "iterations": args.iterations,
        "result": result,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.scenario != "queue" and result["summary"]["failed"]:
        return 1
    if args.scenario == "queue" and args.wait:
        failed_jobs = [
            job for job in result["jobs"]
            if job.get("status") not in {"done", "deleted"}
        ]
        if failed_jobs:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
