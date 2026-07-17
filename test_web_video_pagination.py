import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class WebVideoPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.styles = (ROOT / "web" / "batch.css").read_text(encoding="utf-8")

    def test_results_have_server_backed_pagination(self) -> None:
        self.assertIn('id="video-pagination"', self.html)
        self.assertIn("const importPageSize = 12", self.script)
        self.assertIn("page=${page}&page_size=${importPageSize}", self.script)
        self.assertIn("function renderImportPagination(pagination)", self.script)
        self.assertIn(".video-pagination", self.styles)

    def test_cards_show_views_and_readable_publication_date(self) -> None:
        self.assertIn("function formatViewCount(value)", self.script)
        self.assertIn("function formatPublicationDate(value)", self.script)
        self.assertIn("formatViewCount(item.view_count)", self.script)
        self.assertIn(
            "formatPublicationDate(item.published_at || item.upload_date)",
            self.script,
        )

    def test_batch_selection_is_explicitly_limited_to_page(self) -> None:
        self.assertIn(">Выбрать страницу</button>", self.html)
        self.assertIn("$('#video-pagination').classList.add('disabled')", self.script)


if __name__ == "__main__":
    unittest.main()
