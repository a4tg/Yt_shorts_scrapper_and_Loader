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

    def test_desktop_sidebar_uses_hover_and_keyboard_navigation_rail(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "sidebar-rail.css").read_text(encoding="utf-8")
        self.assertIn('href="/assets/sidebar-rail.css"', html)
        self.assertIn("@media (min-width: 761px)", styles)
        self.assertIn(".workspace-sidebar:hover", styles)
        self.assertIn(".workspace-sidebar:focus-within", styles)
        self.assertIn("grid-template-columns: 78px minmax(0, 1fr)", styles)
        self.assertIn("width: 250px", styles)
        self.assertIn("prefers-reduced-motion: reduce", styles)

    def test_project_graph_and_flowchart_editor_are_wired_as_workspace_module(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        script = (ROOT / "web" / "modules" / "project-graph.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "project-graph.css").read_text(encoding="utf-8")
        editor_styles = (ROOT / "web" / "project-graph-editor.css").read_text(encoding="utf-8")
        polish_styles = (ROOT / "web" / "project-graph-polish.css").read_text(encoding="utf-8")
        for marker in (
            'data-page="graph"',
            'id="project-graph-viewport"',
            'id="project-diagram-viewport"',
            'data-node-kind="decision"',
            'data-graph-action="add"',
            'id="project-graph-history-dialog"',
            'href="/assets/project-graph-polish.css"',
            'id="project-graph-summary"',
            'data-graph-action="zoom-in"',
            'data-graph-action="zoom-out"',
        ):
            self.assertIn(marker, html)
        self.assertIn("registerModule('project-graph'", entrypoint)
        for marker in (
            "loadGraph", "createLink", "saveDiagram", "undo", "redo", "EventSource",
            "project-diagram-edges", "project-diagram-visibility", "review:focus",
            "saveGraphState", "addCustomNode", "deleteCustomNode", "showGraphHistory",
            "restoreGraphRevision", "/graph-state", "/graph-history",
        ):
            self.assertIn(marker, script)
        self.assertIn("if (bounds.width < 1 || bounds.height < 1) return false", script)
        self.assertIn("const projectChanged = projectId !== state.projectId", script)
        self.assertIn("new MutationObserver", script)
        self.assertIn("state.projectId || context?.project?.id", script)
        self.assertIn("GRAPH_LAYOUT_VERSION = 3", script)
        self.assertIn("visibleGraphNodes", script)
        for selector in (".project-graph-node", ".project-diagram-node", ".project-diagram-edge", ".project-graph-minimap"):
            self.assertIn(selector, styles)
        self.assertIn(".project-diagram-edge-list", editor_styles)
        self.assertIn(".project-graph-custom-form", editor_styles)
        self.assertIn(".graph-history-dialog", editor_styles)
        self.assertIn(".project-graph-toolbar-secondary", polish_styles)
        self.assertIn(".has-graph-selection .project-graph-inspector", polish_styles)

    def test_decision_intelligence_attention_center_is_wired(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        entrypoint = (ROOT / "web" / "workspace-depth.js").read_text(encoding="utf-8")
        script = (ROOT / "web" / "modules" / "decision-intelligence.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "decision-intelligence.css").read_text(encoding="utf-8")
        for marker in ('data-page="attention"', 'id="attention-score"', 'id="decision-signal-list"', 'id="project-briefing"'):
            self.assertIn(marker, html)
        self.assertIn("registerModule('decision-intelligence'", entrypoint)
        for marker in ("/attention", "/insights/extract", "/briefings", "EventSource", "patchInsight"):
            self.assertIn(marker, script)
        self.assertIn("state.projectId || context?.project?.id", script)
        self.assertIn("new MutationObserver", script)
        self.assertIn("pageWasHidden && !pageIsHidden", script)
        self.assertIn("pageObserver.disconnect()", script)
        for selector in (".attention-score-ring", ".decision-signal", ".attention-queue-panel", ".decision-create-dialog"):
            self.assertIn(selector, styles)


if __name__ == "__main__":
    unittest.main()
