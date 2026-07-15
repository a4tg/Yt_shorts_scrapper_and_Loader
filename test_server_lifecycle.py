import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import server


class VideoLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)
        self.job_id = uuid.uuid4().hex
        self.video_dir = server.VIDEOS_DIR / self.job_id
        self.video_dir.mkdir(parents=True)
        (self.video_dir / "ready.mp4").write_bytes(b"test-video")
        self.job = {
            "id": self.job_id,
            "kind": "download",
            "status": "done",
            "message": "Готово",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ready_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "result": {"filename": "ready.mp4"},
        }
        with server.manager.lock:
            server.manager.jobs[self.job_id] = self.job
            server.manager._save(self.job)

    def tearDown(self) -> None:
        with server.manager.lock:
            server.manager.jobs.pop(self.job_id, None)
        (server.JOBS_DIR / f"{self.job_id}.json").unlink(missing_ok=True)
        shutil.rmtree(self.video_dir, ignore_errors=True)

    def test_download_requires_ticket_and_manual_delete_removes_file(self) -> None:
        job_response = self.client.get(f"/api/jobs/{self.job_id}")
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(
            job_response.json()["download_ticket_url"],
            f"/api/videos/{self.job_id}/download-ticket",
        )

        self.assertEqual(self.client.get(f"/api/videos/{self.job_id}/download").status_code, 409)
        ticket = self.client.post(f"/api/videos/{self.job_id}/download-ticket")
        self.assertEqual(ticket.status_code, 200)
        self.assertEqual(self.client.get(ticket.json()["download_url"]).content, b"test-video")
        self.assertIn("delete_at", server.manager.get(self.job_id))

        deleted = self.client.delete(ticket.json()["delete_url"])
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(self.video_dir.exists())
        self.assertEqual(self.client.get(f"/api/videos/{self.job_id}/download").status_code, 410)

    def test_expired_download_is_deleted_automatically(self) -> None:
        server.manager.update(
            self.job_id,
            delete_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )

        server.manager._expire_downloads()

        self.assertFalse(self.video_dir.exists())
        self.assertEqual(server.manager.get(self.job_id)["status"], "deleted")

    def test_batch_result_is_downloaded_as_zip(self) -> None:
        (self.video_dir / "ready.mp4").unlink()
        (self.video_dir / "overlay_variants.zip").write_bytes(b"zip-data")
        server.manager.update(
            self.job_id,
            result={"filename": "overlay_variants.zip", "overlay_count": 2, "format": "zip"},
        )
        ticket = self.client.post(f"/api/videos/{self.job_id}/download-ticket").json()

        response = self.client.get(ticket["download_url"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertEqual(response.content, b"zip-data")


class ConstructorPayloadTests(unittest.TestCase):
    def test_constructor_coordinates_are_bounded(self) -> None:
        payload = server.DownloadRequest(
            url="https://youtu.be/abcdefghijk",
            position_x=75,
            position_y=20,
        )
        self.assertEqual((payload.position_x, payload.position_y), (75, 20))

        with self.assertRaises(ValidationError):
            server.DownloadRequest(
                url="https://youtu.be/abcdefghijk",
                position_x=101,
            )

    def test_api_forwards_constructor_position_to_worker(self) -> None:
        with patch.object(
            server.manager,
            "create",
            return_value={"id": "test-job", "status": "queued"},
        ) as create:
            response = TestClient(server.app).post(
                "/api/videos/download",
                json={
                    "url": "https://youtu.be/abcdefghijk",
                    "position_x": 12,
                    "position_y": 34,
                    "width_percent": 40,
                },
            )

        self.assertEqual(response.status_code, 202)
        worker_args = create.call_args.args[1]
        self.assertEqual(worker_args["position_x"], 12)
        self.assertEqual(worker_args["position_y"], 34)
        self.assertEqual(worker_args["width_percent"], 40)

    def test_api_forwards_multiple_overlays_in_selection_order(self) -> None:
        tokens = [uuid.uuid4().hex, uuid.uuid4().hex]
        paths = [
            server.LOGOS_DIR / f"{tokens[0]}_first.png",
            server.LOGOS_DIR / f"{tokens[1]}_second.gif",
        ]
        for path in paths:
            path.write_bytes(b"overlay")
        try:
            with patch.object(
                server.manager,
                "create",
                return_value={"id": "batch-job", "status": "queued"},
            ) as create:
                response = TestClient(server.app).post(
                    "/api/videos/download",
                    json={
                        "url": "https://youtu.be/abcdefghijk",
                        "logo_tokens": tokens,
                    },
                )
        finally:
            for path in paths:
                path.unlink(missing_ok=True)

        self.assertEqual(response.status_code, 202)
        overlays = create.call_args.args[1]["overlays"]
        self.assertEqual([item["name"] for item in overlays], ["first.png", "second.gif"])
        self.assertEqual([item["path"] for item in overlays], [str(path) for path in paths])

    def test_rejects_more_than_ten_overlays(self) -> None:
        with self.assertRaises(ValidationError):
            server.DownloadRequest(
                url="https://youtu.be/abcdefghijk",
                logo_tokens=[uuid.uuid4().hex for _ in range(11)],
            )


if __name__ == "__main__":
    unittest.main()
