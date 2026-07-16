import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WorkspaceDepthFoundationTests(unittest.TestCase):
    def test_module_entrypoint_and_feature_flags_are_wired(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        flags = (ROOT / "web" / "core" / "feature-flags.js").read_text(encoding="utf-8")
        self.assertIn('src="/assets/workspace-depth.js" type="module"', html)
        self.assertIn("window.AAPWorkspaceDepth", entrypoint)
        for name in (
            "chat_anywhere",
            "asset_viewer",
            "asset_reviews",
            "project_graph",
            "decision_intelligence",
        ):
            self.assertIn(f"'{name}'", flags)

    def test_foundation_exposes_event_bus_router_and_legacy_bridge(self) -> None:
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        router = (ROOT / "web" / "core" / "context-router.js").read_text(encoding="utf-8")
        self.assertIn("window.AAPLegacyApp", app)
        self.assertIn("aap:context-change", app)
        self.assertIn("workspaceBus", entrypoint)
        self.assertIn("buildWorkspaceHash", router)
        self.assertIn("route:change", router)


if __name__ == "__main__":
    unittest.main()
