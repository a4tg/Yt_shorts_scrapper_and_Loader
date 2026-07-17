import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import server
from auth_service import attempt_limiter
from saas_models import ContentAttachment, Job, Overlay


def authenticated_client() -> tuple[TestClient, str]:
    client = TestClient(server.app)
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": f"user-{uuid.uuid4().hex}@example.com",
            "password": "correct horse battery staple",
        },
    )
    if response.status_code != 201:
        raise AssertionError(response.text)
    attempt_limiter.clear("register:testclient")
    client.headers.update(
        {
            "Origin": "http://testserver",
            "X-CSRF-Token": client.cookies.get("yt_loader_csrf"),
        }
    )
    return client, response.json()["id"]


class VideoLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.user_id = authenticated_client()
        self.job_id = uuid.uuid4().hex
        self.video_dir = server.VIDEOS_DIR / self.job_id
        self.video_dir.mkdir(parents=True)
        (self.video_dir / "ready.mp4").write_bytes(b"test-video")
        server.manager.create(
            "download",
            {
                "url": "https://youtu.be/abcdefghijk",
                "channel_name": "Тестовый канал",
                "video_title": "Тестовый ролик",
            },
            self.user_id,
            job_id=self.job_id,
        )
        server.manager.update(
            self.job_id,
            status="done",
            message="Готово",
            ready_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            result={"filename": "ready.mp4"},
        )

    def tearDown(self) -> None:
        with server.SessionLocal() as db:
            record = db.get(Job, self.job_id)
            if record:
                db.delete(record)
                db.commit()
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

    def test_profile_library_lists_stored_own_videos_with_channel_folder(self) -> None:
        other_client, other_user_id = authenticated_client()
        other_job_id = uuid.uuid4().hex
        other_dir = server.VIDEOS_DIR / other_job_id
        other_dir.mkdir(parents=True)
        (other_dir / "other.mp4").write_bytes(b"other-video")
        server.manager.create(
            "download",
            {
                "url": "https://youtu.be/12345678901",
                "channel_name": "Чужой канал",
                "video_title": "Чужой ролик",
            },
            other_user_id,
            job_id=other_job_id,
        )
        server.manager.update(
            other_job_id,
            status="done",
            result={"filename": "other.mp4"},
        )
        try:
            response = self.client.get("/api/videos/library")
            self.assertEqual(response.status_code, 200)
            items = response.json()
            self.assertEqual([item["id"] for item in items], [self.job_id])
            self.assertEqual(items[0]["channel_name"], "Тестовый канал")
            self.assertEqual(items[0]["video_title"], "Тестовый ролик")
            self.assertEqual(items[0]["stored_filename"], "ready.mp4")
            self.assertEqual(items[0]["stored_size_bytes"], len(b"test-video"))
            self.assertEqual(
                items[0]["download_ticket_url"],
                f"/api/videos/{self.job_id}/download-ticket",
            )
            self.assertEqual(
                [item["id"] for item in other_client.get("/api/videos/library").json()],
                [other_job_id],
            )
        finally:
            with server.SessionLocal() as db:
                record = db.get(Job, other_job_id)
                if record:
                    db.delete(record)
                    db.commit()
            shutil.rmtree(other_dir, ignore_errors=True)

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
    def test_mov_overlay_gets_private_browser_preview(self) -> None:
        client, user_id = authenticated_client()
        workspace = client.get("/api/workspaces").json()[0]
        project = client.get(f"/api/workspaces/{workspace['id']}/projects").json()[0]

        def write_preview(_source, destination) -> None:
            destination.write_bytes(b"png-preview")

        with (
            patch.object(server, "is_supported_overlay", return_value=True),
            patch.object(server, "create_overlay_preview", side_effect=write_preview),
        ):
            response = client.post(
                "/api/logos",
                data={"project_id": project["id"]},
                files={"file": ("animated.mov", b"mov-content", "video/quicktime")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["name"], "animated.mov")
        self.assertEqual(payload["preview_url"], f"/api/logos/{payload['token']}/preview")
        self.assertEqual(len(payload["library_attachment_id"]), 36)
        self.assertNotEqual(payload["library_attachment_id"], payload["token"])
        self.assertEqual(payload["project_id"], project["id"])
        preview = client.get(payload["preview_url"])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/png")
        self.assertEqual(preview.content, b"png-preview")

        library = client.get(f"/api/projects/{project['id']}/library")
        self.assertEqual(library.status_code, 200)
        library_asset = next(
            item for item in library.json() if item["id"] == payload["library_attachment_id"]
        )
        self.assertEqual(library_asset["source_type"], "overlay")
        self.assertEqual(library_asset["name"], "animated.mov")

        with server.SessionLocal() as db:
            record = db.get(Overlay, payload["token"])
            attachment = db.get(ContentAttachment, payload["library_attachment_id"])
            overlay_path = server.Path(record.storage_path)
            self.assertEqual(record.storage_path, attachment.storage_path)
            self.assertEqual(attachment.asset_key, payload["token"])
            self.assertTrue(overlay_path.is_relative_to(server.CONTENT_DIR))

        deleted = client.delete(
            f"/api/content-attachments/{payload['library_attachment_id']}"
        )
        self.assertEqual(deleted.status_code, 204)
        with server.SessionLocal() as db:
            self.assertIsNone(db.get(Overlay, payload["token"]))
            self.assertIsNone(db.get(ContentAttachment, payload["library_attachment_id"]))
        self.assertFalse(overlay_path.exists())
        self.assertFalse(
            overlay_path.with_name(f"{payload['token']}_preview.png").exists()
        )

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

        full_width = server.DownloadRequest(
            url="https://youtu.be/abcdefghijk",
            width_percent=100,
        )
        self.assertEqual(full_width.width_percent, 100)
        with self.assertRaises(ValidationError):
            server.DownloadRequest(
                url="https://youtu.be/abcdefghijk",
                width_percent=101,
            )

    def test_api_forwards_constructor_position_to_worker(self) -> None:
        client, user_id = authenticated_client()
        with patch.object(
            server.manager,
            "create_batch",
            return_value={
                "jobs": [{"id": "test-job", "status": "queued"}],
                "batch_id": "batch",
            },
        ) as create_batch:
            response = client.post(
                "/api/videos/download",
                json={
                    "url": "https://youtu.be/abcdefghijk",
                    "position_x": 12,
                    "position_y": 34,
                    "width_percent": 40,
                    "metadata_mode": "synthetic",
                    "channel_name": "Канал для медиатеки",
                    "video_title": "Название для медиатеки",
                },
            )

        self.assertEqual(response.status_code, 202)
        worker_args = create_batch.call_args.args[1][0]
        self.assertEqual(worker_args["position_x"], 12)
        self.assertEqual(worker_args["position_y"], 34)
        self.assertEqual(worker_args["width_percent"], 40)
        self.assertEqual(worker_args["metadata_mode"], "synthetic")
        self.assertEqual(worker_args["channel_name"], "Канал для медиатеки")
        self.assertEqual(worker_args["video_title"], "Название для медиатеки")
        self.assertEqual(create_batch.call_args.kwargs["owner_id"], user_id)

    def test_api_forwards_multiple_overlays_in_selection_order(self) -> None:
        client, user_id = authenticated_client()
        tokens = [uuid.uuid4().hex, uuid.uuid4().hex]
        user_logos_dir = server.LOGOS_DIR / user_id
        user_logos_dir.mkdir(parents=True, exist_ok=True)
        paths = [
            user_logos_dir / f"{tokens[0]}_first.png",
            user_logos_dir / f"{tokens[1]}_second.gif",
        ]
        for path in paths:
            path.write_bytes(b"overlay")
        with server.SessionLocal() as db:
            db.add_all(
                [
                    Overlay(
                        id=token,
                        user_id=user_id,
                        original_name=path.name.split("_", 1)[1],
                        storage_path=str(path.resolve()),
                        size_bytes=path.stat().st_size,
                    )
                    for token, path in zip(tokens, paths)
                ]
            )
            db.commit()
        try:
            with patch.object(
                server.manager,
                "create_batch",
                return_value={
                    "jobs": [{"id": "batch-job", "status": "queued"}],
                    "batch_id": "batch",
                },
            ) as create_batch:
                response = client.post(
                    "/api/videos/download",
                    json={
                        "url": "https://youtu.be/abcdefghijk",
                        "logo_tokens": tokens,
                    },
                )
        finally:
            with server.SessionLocal() as db:
                for token in tokens:
                    record = db.get(Overlay, token)
                    if record:
                        db.delete(record)
                db.commit()
            for path in paths:
                path.unlink(missing_ok=True)
            user_logos_dir.rmdir()

        self.assertEqual(response.status_code, 202)
        overlays = create_batch.call_args.args[1][0]["overlays"]
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
