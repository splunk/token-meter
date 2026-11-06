import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter import dashboard_page
from tests.dashboard_page import DASHBOARD_MANIFEST, ROOT


def write_parts(root, parts, manifest_lines=None):
    base = Path(root, "dashboard")
    for name, text in parts.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    lines = manifest_lines if manifest_lines is not None else list(parts)
    (base / "page.manifest").write_text("# comment\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return str(base / "page.manifest")


class DashboardAssemblyTests(unittest.TestCase):
    def test_parts_concatenate_in_manifest_order_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_parts(tmp, {"b.html": "<b>\r\n", "a/one.js": "x=1;\n"},
                                   ["b.html", "a/one.js"])
            self.assertEqual(dashboard_page.assemble(manifest), "<b>\r\nx=1;\n")

    def test_assembly_refreshes_when_a_part_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_parts(tmp, {"a.html": "one"})
            self.assertEqual(dashboard_page.assemble(manifest), "one")
            part = Path(tmp, "dashboard", "a.html")
            part.write_text("two!", encoding="utf-8")
            os.utime(part, ns=(1, 1))
            self.assertEqual(dashboard_page.assemble(manifest), "two!")

    def test_manifest_rejects_paths_outside_the_dashboard_folder(self):
        for entry in ("../meter.py", "/etc/passwd", "a/../../x", "a\\b.js", "./a.js"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                manifest = write_parts(tmp, {"a.html": "x"}, [entry])
                with self.assertRaises(ValueError):
                    dashboard_page.assemble(manifest)

    def test_manifest_rejects_duplicate_and_empty_part_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                dashboard_page.assemble(write_parts(tmp, {"a.html": "x"}, ["a.html", "a.html"]))
            with self.assertRaises(ValueError):
                dashboard_page.assemble(write_parts(tmp, {"a.html": "x"}, []))

    def test_repository_manifest_lists_every_dashboard_part_exactly_once(self):
        base = Path(ROOT, "dashboard")
        listed = dashboard_page.part_names(DASHBOARD_MANIFEST)
        on_disk = sorted(
            path.relative_to(base).as_posix()
            for path in base.rglob("*")
            if path.is_file() and path.name != "page.manifest"
        )
        self.assertEqual(sorted(listed), on_disk)

    def test_assembled_page_is_one_document_with_one_inline_script(self):
        page = dashboard_page.assemble(DASHBOARD_MANIFEST)
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertTrue(page.endswith("</script></body></html>\n"))
        self.assertEqual(page.count("\n<script>\n"), 1)
        self.assertEqual(page.count("<style>\n"), 1)


class DashboardServingTests(unittest.TestCase):
    def test_server_looks_for_the_dashboard_manifest_beside_the_source(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKEN_METER_PAGE", None)
            candidates = meter.page_candidates()
        self.assertEqual(candidates[0], dashboard_page.manifest_path(meter._SOURCE_ROOT))
        self.assertNotIn("page.html", " ".join(candidates))

    def test_page_bytes_assemble_a_manifest_and_read_an_explicit_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_parts(tmp, {"a.html": "<p>", "b.js": "1"})
            self.assertEqual(meter.page_bytes(manifest), b"<p>1")
            self.assertEqual(meter.page_root(manifest), os.path.abspath(tmp))
            single = Path(tmp, "page.html")
            single.write_text("single", encoding="utf-8")
            self.assertEqual(meter.page_bytes(str(single)), b"single")
            self.assertEqual(meter.page_root(str(single)), tmp)

    def test_missing_part_serves_the_setup_error_instead_of_a_partial_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_parts(tmp, {"a.html": "x"}, ["a.html", "gone.js"])
            self.assertIsNone(meter.page_bytes(manifest))

    def test_bundled_assets_resolve_beside_the_dashboard_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_parts(tmp, {"a.html": "x"})
            font = Path(tmp, "assets", "fonts", "Tektur-Variable.ttf")
            font.parent.mkdir(parents=True)
            font.write_bytes(b"font")
            with mock.patch.object(meter, "page_path", return_value=manifest):
                self.assertEqual(
                    meter.dashboard_asset_path("/assets/fonts/Tektur-Variable.ttf"),
                    str(font),
                )


if __name__ == "__main__":
    unittest.main()
