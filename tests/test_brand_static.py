"""The bridge serves the six shipped brand files, each with its content type, and nothing else under assets/."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bridge  # reuses the real-server fixture (module import: its tests are not collected here)

WEB = Path(__file__).resolve().parents[1] / "glitch-idea" / "web"
BRAND = {
    "/assets/fonts/BlastworksSans-Regular.woff2": "font/woff2",
    "/assets/fonts/BlastworksSans-SemiBold.woff2": "font/woff2",
    "/assets/fonts/BlastworksSans-ExtraBold.woff2": "font/woff2",
    "/assets/fonts/BlastworksSans-UNLICENSE.txt": "text/plain; charset=utf-8",
    "/assets/logo.svg": "image/svg+xml",
    "/assets/bwpm/bundle.css": "text/css; charset=utf-8",
}


class BrandStatic(unittest.TestCase):
    # Borrow the real-server fixture without inheriting (and re-running) the whole bridge suite.
    setUp, stop, request = test_bridge.BridgeTests.setUp, test_bridge.BridgeTests.stop, test_bridge.BridgeTests.request

    def test_each_brand_file_is_served_with_type_and_csp(self):
        for path, content_type in BRAND.items():
            with self.subTest(path=path):
                status, body, headers = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], content_type)
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(body, (WEB / path.lstrip("/")).read_bytes())

    def test_unlisted_asset_paths_are_404(self):
        for path in ("/assets/fonts/evil.woff2", "/assets/", "/assets", "/assets/fonts/",
                     "/assets/NOTICE.md", "/assets/fonts/../logo.svg", "/assets/bwpm/other.css"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
