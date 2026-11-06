import re
import unittest
from pathlib import Path

import meter
from tests.dashboard_page import DASHBOARD_PAGE


ROOT = Path(__file__).resolve().parents[2]


class KiroExtensionBudgetTests(unittest.TestCase):
    def test_shared_domain_platform_http_and_generic_clients_need_no_kiro_branch(self):
        paths = [
            *(ROOT / "token_meter" / "domain").glob("*.py"),
            *(ROOT / "token_meter" / "platforms").glob("*.py"),
            ROOT / "token_meter" / "web" / "server.py",
            Path(DASHBOARD_PAGE),
            ROOT / "menubar" / "TokenMeterMenuBar.swift",
            ROOT / "menubar" / "token_meter_tray.py",
        ]
        for path in paths:
            source = path.read_text().lower()
            self.assertNotRegex(source, r"\b(?:if|elif|case|switch)\b[^\n]{0,120}\bkiro\b", path)

    def test_composition_contains_no_kiro_runtime_dispatch_condition(self):
        composition = [ROOT / "token_meter" / "app.py", *(ROOT / "token_meter" / "app_helpers").glob("*.py")]
        source = "\n".join(path.read_text().lower() for path in composition)
        self.assertIsNone(re.search(
            r"\b(?:if|elif)\b[^\n]{0,120}(?:provider|runtime|client)[^\n]{0,80}\bkiro\b",
            source,
        ))

    def test_agent_runtime_resolution_is_registry_driven(self):
        for runtime_id in meter.runtime_registry().runtime_ids:
            with self.subTest(runtime_id=runtime_id):
                self.assertEqual(meter.agent_provider(runtime_id), runtime_id)


if __name__ == "__main__":
    unittest.main()
