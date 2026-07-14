import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

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


if __name__ == "__main__":
    unittest.main()
