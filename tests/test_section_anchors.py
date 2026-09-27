import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SectionAnchorTests(unittest.TestCase):
    def test_every_local_fragment_points_to_an_existing_id(self):
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        fragments = set(re.findall(r'href="#([^"]+)"', page))
        ids = set(re.findall(r'id="([^"]+)"', page))
        self.assertIn("engravings", fragments)
        self.assertIn("engravings", ids)
        self.assertFalse(fragments - ids, f"Missing fragment targets: {fragments - ids}")

    def test_fragment_navigation_is_reapplied_after_layout_settles(self):
        page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        self.assertIn("app.js?v=section-anchors-20260927", page)
        self.assertIn("function settlePageFragment(hash)", script)
        self.assertIn('event.target.closest("a[href^=\'#\']")', script)
        self.assertIn('window.addEventListener("hashchange"', script)
        self.assertIn('window.addEventListener("load"', script)
        self.assertIn('behavior: "auto", block: "start"', script)


if __name__ == "__main__":
    unittest.main()
