import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebBillingTests(unittest.TestCase):
    def test_billing_panel_is_wired_to_api(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="billing-available"', html)
        self.assertIn('id="billing-plans"', html)
        self.assertIn('id="billing-credit-packages"', html)
        self.assertIn('id="billing-entitlement"', html)
        self.assertIn('id="billing-limits"', html)
        self.assertIn('id="checkout-dialog"', html)
        self.assertIn('id="checkout-recurring-consent"', html)
        self.assertIn('id="checkout-offer-consent"', html)
        self.assertIn('id="register-legal-consent"', html)
        self.assertIn('/assets/billing.css', html)
        self.assertIn("api('/api/billing/summary')", script)
        self.assertIn("api('/api/billing/plans')", script)
        self.assertIn("api('/api/billing/ledger?limit=8')", script)
        self.assertIn("api('/api/payments/credit-packages')", script)
        self.assertIn("api('/api/payments/credit-packages/checkout'", script)
        self.assertIn("summary.trial_expires_at", script)
        self.assertIn("recurring_consent: true", script)
        self.assertIn("offer_accepted: true", script)
        self.assertIn("function openCheckoutDialog(plan, sourceButton)", script)
        self.assertIn("summary.subscription_status === 'grace'", script)
        self.assertIn("Доступ временно сохранён", script)

    def test_landing_uses_the_live_commercial_catalog(self) -> None:
        html = (ROOT / "web" / "landing.html").read_text(encoding="utf-8")
        calculator = (ROOT / "web" / "commercial.js").read_text(encoding="utf-8")
        for text in (
            "7 дней",
            "20 кредитов",
            "1 490 ₽",
            "4 490 ₽",
            "9 990 ₽",
            "700 кредитов",
            "1 800 кредитов",
        ):
            self.assertIn(text, html)
        self.assertNotIn("Попробовать 14 дней", html)
        self.assertNotIn("5 стартовых кредитов", html)
        self.assertIn("{ limit: 20, name: 'Пробный'", calculator)
        self.assertIn("{ limit: 700, name: 'Team'", calculator)
        self.assertIn("{ limit: 1800, name: 'Agency'", calculator)


if __name__ == "__main__":
    unittest.main()
