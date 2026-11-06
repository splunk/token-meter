import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_IDS = ("claude", "codex", "cursor", "opencode", "kiro")


class ArchitectureGuardTests(unittest.TestCase):
    def test_composition_contains_no_superseded_legacy_function_bodies(self):
        composition = [ROOT / "token_meter" / "app.py", *(ROOT / "token_meter" / "app_helpers").glob("*.py")]
        names = [
            node.name
            for path in composition
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_legacy_")
        ]
        self.assertEqual(names, [])

    def test_tests_never_patch_moved_helpers_through_meter(self):
        # Patching meter.NAME rebinds the name in app.py only; helpers that call
        # NAME inside app_helpers would keep the real one. Patch the owning
        # module (token_meter.app_helpers.<module>) instead.
        moved = set()
        for path in (ROOT / "token_meter" / "app_helpers").glob("*.py"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    moved.add(node.name)
                elif isinstance(node, ast.Assign):
                    moved.update(t.id for t in node.targets if isinstance(t, ast.Name))
        pattern = re.compile(
            r"patch\.object\(\s*(?:meter|app)\s*,\s*[\"'](\w+)"
            r"|patch\(\s*[\"'](?:meter|token_meter\.app)\.(\w+)"
        )
        offenders = []
        for path in (ROOT / "tests").rglob("*.py"):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                name = match.group(1) or match.group(2)
                if name in moved:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(offenders, [])

    def test_app_helpers_never_import_the_composition_module(self):
        # app.py re-exports every helper name; an import back would be circular
        # and would let helpers depend on names tests patch on ``meter``.
        for path in (ROOT / "token_meter" / "app_helpers").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"from token_meter import app\b|import token_meter\.app\b|^import meter", path)
            self.assertNotIn("from token_meter.app import", source, path)

    def test_shared_domain_services_and_web_have_no_runtime_dispatch_conditionals(self):
        paths = [
            *(ROOT / "token_meter" / "domain").glob("*.py"),
            *(ROOT / "token_meter" / "services").glob("*.py"),
            *(ROOT / "token_meter" / "web").glob("*.py"),
        ]
        runtime_pattern = "|".join(map(re.escape, RUNTIME_IDS))
        branch = re.compile(
            r"\b(?:if|elif|case|match)\b[^\n]{0,180}\b(?:" + runtime_pattern + r")\b",
            re.IGNORECASE,
        )
        for path in paths:
            self.assertNotRegex(path.read_text(encoding="utf-8"), branch, path)

    def test_public_boundaries_do_not_serialize_internal_dataclasses_wholesale(self):
        paths = [
            ROOT / "token_meter" / "compat.py",
            *(ROOT / "token_meter" / "services").glob("*.py"),
            *(ROOT / "token_meter" / "web").glob("*.py"),
        ]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("dataclasses.asdict", source, path)
            self.assertNotRegex(source, r"\bvars\([^\n]*(?:source|session|locator)", path)
            self.assertNotRegex(source, r"(?:source|session|locator)\.__dict__", path)

    def test_contributor_guide_has_executable_extension_recipes(self):
        guide = (ROOT / "specs" / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for heading in (
            "Adding a runtime",
            "Adding a model",
            "Adding a quota adapter",
            "Adding a platform",
            "Adding a telemetry mapping",
        ):
            self.assertIn(heading, guide)
        self.assertIn("python3 -m unittest discover -s tests -v", guide)
        self.assertIn("runtime-manifest.txt", guide)


if __name__ == "__main__":
    unittest.main()
