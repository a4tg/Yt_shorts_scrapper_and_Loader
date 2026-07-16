from __future__ import annotations

import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], parent: Node | None = None) -> None:
        self.tag = tag
        self.attrs = {name: value if value is not None else "" for name, value in attrs}
        self.parent = parent
        self.children: list[Node | str] = []

    def descendants(self, tag: str | None = None) -> list[Node]:
        found: list[Node] = []
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    found.append(child)
                found.extend(child.descendants(tag))
        return found

    def text(self) -> str:
        return " ".join(
            child.text() if isinstance(child, Node) else child.strip()
            for child in self.children
            if (child.text().strip() if isinstance(child, Node) else child.strip())
        ).strip()

    def has_ancestor(self, tag: str) -> bool:
        current = self.parent
        while current is not None:
            if current.tag == tag:
                return True
            current = current.parent
        return False


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].children.append(data)


def parse_document(name: str) -> tuple[str, Node]:
    source = (WEB / name).read_text(encoding="utf-8")
    parser = TreeParser()
    parser.feed(source)
    return source, parser.root


class VisualQualityTests(unittest.TestCase):
    def test_public_documents_have_accessible_structure(self) -> None:
        for name in ("landing.html", "index.html"):
            with self.subTest(document=name):
                source, root = parse_document(name)
                html = root.descendants("html")[0]
                self.assertEqual(html.attrs.get("lang"), "ru")
                self.assertTrue(root.descendants("title")[0].text())
                self.assertIn('name="viewport"', source)
                self.assertTrue(root.descendants("main"), "Document needs a main landmark")
                self.assertTrue(any("skip-link" in node.attrs.get("class", "").split() for node in root.descendants("a")))

                elements = root.descendants()
                ids = [node.attrs["id"] for node in elements if node.attrs.get("id")]
                duplicates = [value for value, count in Counter(ids).items() if count > 1]
                self.assertEqual(duplicates, [], f"Duplicate ids: {duplicates}")
                id_set = set(ids)
                for node in elements:
                    for attribute in ("aria-controls", "aria-labelledby"):
                        for reference in node.attrs.get(attribute, "").split():
                            self.assertIn(reference, id_set, f"{attribute} points to missing #{reference}")

                labels_for = {node.attrs.get("for") for node in root.descendants("label") if node.attrs.get("for")}
                for control in [node for node in elements if node.tag in {"input", "select", "textarea"}]:
                    if control.attrs.get("type", "").lower() == "hidden":
                        continue
                    accessible = bool(
                        control.attrs.get("aria-label")
                        or control.attrs.get("aria-labelledby")
                        or control.has_ancestor("label")
                        or control.attrs.get("id") in labels_for
                    )
                    self.assertTrue(accessible, f"Unlabelled <{control.tag}> #{control.attrs.get('id', '')}")

                for button in root.descendants("button"):
                    self.assertTrue(
                        button.text() or button.attrs.get("aria-label") or button.attrs.get("aria-labelledby"),
                        f"Button has no accessible name: #{button.attrs.get('id', '')}",
                    )
                for image in root.descendants("img"):
                    self.assertIn("alt", image.attrs, f"Image needs alt: {image.attrs.get('src', '')}")
                    if image.attrs.get("src"):
                        self.assertTrue(image.attrs.get("width") and image.attrs.get("height"), f"Static image needs dimensions: {image.attrs['src']}")
                for script in root.descendants("script"):
                    if script.attrs.get("src"):
                        self.assertIn("defer", script.attrs, f"Script blocks parsing: {script.attrs['src']}")

    def test_local_assets_exist_and_are_not_duplicated(self) -> None:
        for name in ("landing.html", "index.html"):
            _, root = parse_document(name)
            references: list[str] = []
            for node in root.descendants():
                reference = node.attrs.get("src") or node.attrs.get("href")
                if reference and reference.startswith("/assets/"):
                    references.append(reference)
                    self.assertTrue((WEB / reference.removeprefix("/assets/")).is_file(), f"Missing {reference}")
            linked = [reference for reference in references if not reference.endswith("brand-mark.svg")]
            duplicates = [value for value, count in Counter(linked).items() if count > 1]
            self.assertEqual(duplicates, [], f"Duplicate asset requests in {name}: {duplicates}")

    def test_motion_and_accessibility_styles_remain_available(self) -> None:
        motion_files = [
            "motion-system.css", "landing-motion.css", "product-demo.css", "commercial.css",
            "scroll-story.css", "app-motion.css", "brand-graphics.css", "performance.css",
            "ambient-particles.css", "app-shell-premium.css", "workflow-motion.css",
            "video-workflow-motion.css", "app-polish.css",
        ]
        for name in motion_files:
            with self.subTest(stylesheet=name):
                source = (WEB / name).read_text(encoding="utf-8")
                self.assertEqual(source.count("{"), source.count("}"), "Unbalanced CSS blocks")
                if "@keyframes" in source:
                    self.assertIn("prefers-reduced-motion", source)
        performance = (WEB / "performance.css").read_text(encoding="utf-8")
        self.assertIn("content-visibility: auto", performance)
        self.assertIn("prefers-contrast: more", performance)
        self.assertIn("forced-colors: active", performance)
        self.assertIn(".skip-link", performance)

    def test_ambient_particles_respect_performance_preferences(self) -> None:
        landing = (WEB / "landing.html").read_text(encoding="utf-8")
        particles = (WEB / "ambient-particles.js").read_text(encoding="utf-8")
        self.assertIn('/assets/ambient-particles.js', landing)
        self.assertIn("prefers-reduced-motion: reduce", particles)
        self.assertIn("document.hidden", particles)
        self.assertIn("navigator.connection?.saveData", particles)
        self.assertIn("Math.min(window.devicePixelRatio || 1, 1.5)", particles)

    def test_premium_app_shell_has_progressive_transitions(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        motion = (WEB / "app-motion.js").read_text(encoding="utf-8")
        styles = (WEB / "app-shell-premium.css").read_text(encoding="utf-8")
        self.assertIn('/assets/app-shell-premium.css', index)
        self.assertIn("document.startViewTransition", app)
        self.assertIn("syncNavigationIndicator", motion)
        self.assertIn("prefers-reduced-motion: reduce", styles)
        self.assertIn("view-transition-name: workspace-page", styles)

    def test_workflow_motion_covers_drag_drop_and_view_changes(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        motion = (WEB / "app-motion.js").read_text(encoding="utf-8")
        styles = (WEB / "workflow-motion.css").read_text(encoding="utf-8")
        self.assertIn('/assets/workflow-motion.css', index)
        self.assertIn("content-is-dragging", app)
        self.assertIn("contentCardMoved", motion)
        self.assertIn("transitionContentView", motion)
        self.assertIn("view-transition-name: content-workflow-view", styles)
        self.assertIn("prefers-reduced-motion: reduce", styles)

    def test_video_workflow_exposes_real_job_and_batch_states(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        motion = (WEB / "app-motion.js").read_text(encoding="utf-8")
        styles = (WEB / "video-workflow-motion.css").read_text(encoding="utf-8")
        self.assertIn('/assets/video-workflow-motion.css', index)
        self.assertIn('id="batch-progress"', index)
        self.assertIn("videoJobUpdated", app)
        self.assertIn("batchProgress", motion)
        self.assertIn("aria-valuenow", motion)
        self.assertIn("prefers-reduced-motion: reduce", styles)

    def test_app_polish_preserves_mobile_and_accessibility_fallbacks(self) -> None:
        index = (WEB / "index.html").read_text(encoding="utf-8")
        motion = (WEB / "motion-system.js").read_text(encoding="utf-8")
        styles = (WEB / "app-polish.css").read_text(encoding="utf-8")
        self.assertIn('/assets/app-polish.css', index)
        self.assertIn("max-width: 760px", styles)
        self.assertIn("update: slow", styles)
        self.assertIn("prefers-reduced-motion: reduce", styles)
        self.assertIn("forced-colors: active", styles)
        self.assertIn(".video-stage", motion)

    def test_brand_assets_stay_within_delivery_budget(self) -> None:
        budgets = {
            "og-image.png": 100_000,
            "brand-flow-map.svg": 10_000,
            "brand-empty-state.svg": 6_000,
            "brand-pattern.svg": 4_000,
            "brand-graphics.css": 8_000,
            "performance.css": 10_000,
        }
        for name, maximum in budgets.items():
            with self.subTest(asset=name):
                self.assertLessEqual((WEB / name).stat().st_size, maximum)
        with Image.open(WEB / "og-image.png") as image:
            self.assertEqual(image.size, (1200, 630))
            self.assertEqual(image.mode, "RGB")

    def test_landing_has_complete_social_metadata(self) -> None:
        source = (WEB / "landing.html").read_text(encoding="utf-8")
        for marker in (
            'rel="canonical"', 'property="og:title"', 'property="og:description"',
            'property="og:image"', 'name="twitter:card"', 'name="theme-color"',
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
