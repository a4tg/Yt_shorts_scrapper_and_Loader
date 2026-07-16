import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebAccountTests(unittest.TestCase):
    def test_recovery_and_verification_controls_are_wired(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        for element_id in (
            "forgot-form",
            "reset-form",
            "verification-banner",
            "resend-verification",
            "change-password-form",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("/api/auth/password/forgot", script)
        self.assertIn("/api/auth/password/reset", script)
        self.assertIn("/api/auth/password/change", script)
        self.assertIn("/api/auth/verification/confirm", script)
        self.assertIn("/api/auth/verification/request", script)
        self.assertIn("location.hash.slice(1)", script)
        self.assertIn("syncPasswordRecoveryAvailability", script)
        self.assertNotIn("classList.toggle('hidden', !config.password_reset_enabled)", script)
        self.assertNotIn("event.currentTarget.reset()", script)
        self.assertNotIn("finally { event.currentTarget.disabled", script)
        self.assertIn("support@allasplanned.ru", script)


if __name__ == "__main__":
    unittest.main()
