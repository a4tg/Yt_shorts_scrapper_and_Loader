import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebWorkspaceTests(unittest.TestCase):
    def test_commercial_workspace_sections_are_present(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("All As Planned", html)
        self.assertIn('/assets/workspace.css', html)
        for page in (
            "dashboard",
            "content",
            "documents",
            "library",
            "video",
            "approvals",
            "messages",
            "ai",
            "billing",
            "support",
            "admin",
        ):
            self.assertIn(f'data-page="{page}"', html)
            self.assertIn(f'data-navigate="{page}"', html)

    def test_hash_navigation_keeps_auth_fragments_separate(self) -> None:
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("location.hash.startsWith('#/')", script)
        self.assertIn("workspacePageFromHash()", script)
        self.assertIn("showWorkspacePage(navigationButton.dataset.navigate, true)", script)
        self.assertIn("fragment.get('verify')", script)
        self.assertIn("fragment.get('reset')", script)

    def test_existing_video_and_billing_controls_remain_available(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

        for element_id in (
            "billing-plans",
            "channel-form",
            "video-stage",
            "direct-video-form",
            "results-section",
            "batch-toolbar",
        ):
            self.assertIn(f'id="{element_id}"', html)

    def test_workspace_project_team_and_approval_controls_are_wired(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "workspace-select",
            "create-workspace-button",
            "workspace-projects",
            "create-project-button",
            "workspace-members",
            "add-member-form",
            "approval-stages",
            "save-workflow-button",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("api('/api/workspaces')", script)
        self.assertIn("/approval-workflow`,", script)
        self.assertIn("renderWorkspaceMembers()", script)

    def test_onboarding_and_admin_console_are_wired(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "onboarding-panel",
            "onboarding-steps",
            "admin-nav-button",
            "admin-stats",
            "admin-users-body",
            "admin-payments-body",
            "admin-feedback-body",
            "admin-jobs-body",
            "admin-refunds-body",
            "admin-audit-body",
            "admin-action-dialog",
            "support-form",
            "support-tickets",
            "create-demo-project",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("!state.currentUser?.is_admin", script)
        self.assertIn("api('/api/admin/overview')", script)
        self.assertIn("api('/api/admin/feedback?limit=100')", script)
        self.assertIn("api('/api/admin/jobs?status=error&limit=100')", script)
        self.assertIn("loadOnboarding()", script)
        self.assertIn("api('/api/onboarding/demo'", script)
        self.assertIn("api('/api/feedback'", script)


if __name__ == "__main__":
    unittest.main()
