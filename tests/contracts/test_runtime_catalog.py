import json
import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.contracts import RuntimeDescriptor
from token_meter.services.runtime_catalog import runtime_catalog
from token_meter.runtimes.registry import RuntimeRegistry
from tests.integration.test_application_composition import SyntheticAdapter


def descriptor(runtime_id="synthetic", label="Synthetic",
               capabilities=("sessions",)):
    return RuntimeDescriptor(
        runtime_id, label, frozenset(capabilities),
        "runtime.generic", "runtime-neutral",
    )


class RuntimeCatalogTests(unittest.TestCase):
    def test_synthetic_and_generic_unknown_runtimes_are_bounded_metadata(self):
        catalog = runtime_catalog((descriptor(),))

        self.assertEqual(list(catalog), ["synthetic", "unknown-runtime"])
        self.assertEqual(catalog["synthetic"], {
            "label": "Synthetic",
            "symbol": "runtime.generic",
            "color": "runtime-neutral",
            "capabilities": ["sessions"],
        })
        encoded = json.dumps(catalog)
        self.assertNotIn("private", encoded)
        self.assertLess(len(encoded), 1024)

    def test_rejects_urls_markup_executable_text_and_unknown_capabilities(self):
        for label in (
            "<b>Runtime</b>", "https://example.test", "javascript:alert(1)",
            "Runtime; rm data", "Runtime `command`", "Runtime $(command)",
        ):
            with self.subTest(label=label), self.assertRaises(ValueError):
                runtime_catalog((descriptor(label=label),))
        with self.assertRaises(ValueError):
            runtime_catalog((descriptor(capabilities=("sessions", "execute")),))
        with self.assertRaises(ValueError):
            runtime_catalog((descriptor(runtime_id="unsafe/runtime"),))

    def test_catalog_entry_count_is_hard_bounded(self):
        values = tuple(descriptor("runtime-{}".format(index)) for index in range(16))

        with self.assertRaises(ValueError):
            runtime_catalog(values)

    def test_state_and_menubar_contracts_include_registered_catalog(self):
        registry = RuntimeRegistry((SyntheticAdapter(),))
        with mock.patch.object(meter, "_RUNTIME_REGISTRY", registry):
            state = meter.dashboard_state_payload({"ok": True})
            with mock.patch.object(meter, "cached_session_sources", return_value=([], True)), \
                    mock.patch.object(meter, "provider_quota_snapshots", return_value=[]), \
                    mock.patch.object(meter, "budget_settings", return_value=meter.normalize_budget_settings({})), \
                    mock.patch.object(meter, "STATE", None), \
                    mock.patch.object(meter, "_xsess", {}):
                menubar = meter.menubar_state()

        self.assertIn("synthetic", state["runtime_catalog"])
        self.assertIn("synthetic", menubar["runtime_catalog"])
        self.assertLess(len(json.dumps(menubar)), 64 * 1024)


class CatalogAwareClientSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.page = (cls.root / "page.html").read_text()
        cls.swift = (cls.root / "menubar" / "TokenMeterMenuBar.swift").read_text()
        cls.tray = (cls.root / "menubar" / "token_meter_tray.py").read_text()

    def test_browser_generic_surfaces_use_catalog_with_unknown_fallback(self):
        for marker in (
            "RUNTIME_CATALOG=state?.runtime_catalog||RUNTIME_CATALOG",
            "const runtimeMeta=session=>RUNTIME_CATALOG[runtimeId(session)]",
            "const appFilterLabel=session=>runtimeMeta(session).label",
            "const runtime=runtimeLabel(row)",
            "Object.entries(RUNTIME_CATALOG).filter(([id])=>id!=='unknown-runtime')",
            "function ensureBudgetRuntimeRows()",
            "budgetRuntimeIds().forEach(provider=>",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn(
            "['claude','codex','cursor','gemini','opencode'].includes(row.provider)",
            self.page,
        )
        self.assertNotIn("['claude','codex','cursor','opencode'].forEach(provider=>", self.page)

    def test_native_recent_sessions_use_catalog_and_accessible_fallback(self):
        recent = self.swift[
            self.swift.index("struct RuntimePresentation"):
            self.swift.index("struct MeterSnapshot")
        ]
        self.assertIn('catalog[provider] ?? catalog["unknown-runtime"]', recent)
        self.assertIn('default: return "circle"', recent)
        self.assertNotIn("switch provider.lowercased()", recent)
        self.assertIn('RuntimePresentation.catalog(dict["runtime_catalog"])', self.swift)
        self.assertIn("state.get('runtime_catalog')", self.tray)
        self.assertNotIn('if value == "codex"', self.tray)

    def test_provider_marks_keep_dashboard_labels_and_native_accessibility(self):
        for marker in (
            "const runtimeMarkSvg=session=>",
            "const runtimeBadgeContent=(session,label)=>",
            "runtimeBadgeContent(s,source.label||s.provider||'source')",
            "${runtimeMarkSvg(s)}${esc(s.label||s.provider)}",
            "${runtimeMarkSvg(row)}<span>${esc(runtimeLine)}</span>",
        ):
            self.assertIn(marker, self.page)
        mark_helper = self.page.split("const runtimeMarkSvg=session=>", 1)[1].split(
            "const runtimeBadgeContent=", 1
        )[0]
        self.assertIn("aria-hidden=true", mark_helper)
        self.assertIn('viewBox="0 0 100 100"', mark_helper)
        self.assertIn("M83.7733 42.8087", mark_helper)
        self.assertIn("M25.7146 63.2153", mark_helper)
        self.assertNotIn("http://", mark_helper)
        self.assertNotIn("https://", mark_helper)
        self.assertIn(
            ".runtimeMark{display:block;width:16px;height:16px",
            self.page,
        )
        self.assertIn(
            ".currentSessionRuntime .runtimeMark{width:18px;height:18px}",
            self.page,
        )

        for marker in (
            "private struct StatusTitleSegment",
            "var providerSymbol: String",
            "var providerAccessibilityText: String",
            "private func runtimeMarkImage(symbol: String",
            "symbol: presentation.providerSymbol",
            'let providerSymbol = runtimeCatalog[snapshot.provider]?.symbol ?? "runtime.generic"',
            "let providerAccessibilityText = runtimeCatalog[snapshot.provider]?.label ?? snapshot.provider",
            "providerSymbol: providerSymbol",
            "providerAccessibilityText: providerAccessibilityText",
            "symbol: runtimeCatalog[constrained.provider.id]?.symbol",
            "let accessibilityText = limitsStatusTitle()",
            "accessibilityText: accessibilityText",
            "let presentationWithoutLimits = selectedStatusTitlePresentation()",
            "presentationWithoutLimits.providerSymbol == expectedProviderSymbol",
        ):
            self.assertIn(marker, self.swift)


if __name__ == "__main__":
    unittest.main()
