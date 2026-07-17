import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebBillingTests(unittest.TestCase):
    def test_billing_panel_is_wired_to_api(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="billing-available"', html)
        self.assertIn('id="billing-plans"', html)
        self.assertIn('id="billing-entitlement"', html)
        self.assertIn('id="billing-limits"', html)
        self.assertIn('id="checkout-dialog"', html)
        self.assertIn('id="checkout-recurring-consent"', html)
        self.assertIn('/assets/billing.css', html)
        self.assertIn("api('/api/billing/summary')", script)
        self.assertIn("api('/api/billing/plans')", script)
        self.assertIn("api('/api/billing/ledger?limit=8')", script)
        self.assertIn("summary.trial_expires_at", script)
        self.assertIn("recurring_consent: true", script)
        self.assertIn("function openCheckoutDialog(plan, sourceButton)", script)


if __name__ == "__main__":
    unittest.main()
