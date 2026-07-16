import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebAITests(unittest.TestCase):
    def test_ai_studio_has_text_image_and_clip_tools(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="ai-text-form"', html)
        self.assertIn('id="ai-image-form"', html)
        self.assertIn('id="ai-clips-form"', html)
        self.assertIn('id="ai-video-attachment"', html)

    def test_ai_client_uses_durable_job_api(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("api('/api/ai/text'", script)
        self.assertIn("api('/api/ai/images'", script)
        self.assertIn("api('/api/ai/clips'", script)
        self.assertIn("await pollJob(job.id", script)


if __name__ == "__main__":
    unittest.main()
