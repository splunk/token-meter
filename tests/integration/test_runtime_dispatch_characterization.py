import unittest
from pathlib import Path
from unittest import mock

import meter
from token_meter.runtimes.legacy import LegacyRuntimeAdapter


class LegacyRuntimeDispatchCharacterizationTests(unittest.TestCase):
    def test_runtime_registry_has_the_current_runtimes_in_discovery_order(self):
        self.assertEqual(
            meter.runtime_registry().runtime_ids,
            ("claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes", "grok"),
        )
        self.assertNotIsInstance(meter.runtime_registry().get("claude"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("codex"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("cursor"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("opencode"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("kiro"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("pi"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("hermes"), LegacyRuntimeAdapter)
        self.assertNotIsInstance(meter.runtime_registry().get("grok"), LegacyRuntimeAdapter)

    def test_claude_routes_through_the_native_adapter(self):
        source = {"provider": "claude", "id": "claude-session"}
        expected = {"provider": "claude", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_claude_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_codex_routes_through_the_native_adapter(self):
        source = {"provider": "codex", "id": "codex-session"}
        expected = {"provider": "codex", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_codex_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_cursor_routes_through_the_native_adapter(self):
        source = {"provider": "cursor", "id": "cursor-session"}
        expected = {"provider": "cursor", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_cursor_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_opencode_routes_through_the_native_adapter(self):
        source = {"provider": "opencode", "id": "opencode-session"}
        expected = {"provider": "opencode", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_opencode_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_kiro_routes_through_the_native_adapter(self):
        source = {"provider": "kiro", "id": "kiro-session"}
        expected = {"provider": "kiro", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_kiro_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_pi_routes_through_the_native_adapter(self):
        source = {"provider": "pi", "id": "pi-session"}
        expected = {"provider": "pi", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_pi_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_hermes_routes_through_the_native_adapter(self):
        source = {"provider": "hermes", "id": "hermes-session"}
        expected = {"provider": "hermes", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_hermes_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_grok_routes_through_the_native_adapter(self):
        source = {"provider": "grok", "id": "grok-session"}
        expected = {"provider": "grok", "marker": object()}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "_grok_native_adapter", return_value=adapter):
            self.assertIs(meter.recompute(source), expected)

        adapter.load.assert_called_once()

    def test_string_source_is_resolved_before_runtime_dispatch(self):
        source = {"provider": "codex", "id": "session-1"}
        expected = {"provider": "codex"}
        adapter = mock.Mock()
        adapter.load.return_value = expected

        with mock.patch.object(meter, "source_from_path", return_value=source) as resolver, \
                mock.patch.object(meter, "_codex_native_adapter", return_value=adapter):
            result = meter.recompute("/private/source.jsonl")

        self.assertIs(result, expected)
        resolver.assert_called_once_with("/private/source.jsonl")
        adapter.load.assert_called_once()

    def test_missing_and_unknown_sources_return_none(self):
        self.assertIsNone(meter.recompute(None))
        self.assertIsNone(meter.recompute({"provider": "future-runtime", "id": "future"}))

    def test_discovery_routes_each_runtime_once_in_registry_order(self):
        rows = {
            runtime_id: {"provider": runtime_id, "id": runtime_id + "-session"}
            for runtime_id in ("claude", "codex", "cursor", "opencode", "kiro", "pi", "hermes", "grok")
        }
        opencode_adapter = mock.Mock()
        opencode_adapter.discover_legacy.return_value = (rows["opencode"],)
        cursor_adapter = mock.Mock()
        cursor_adapter.discover_legacy.return_value = (rows["cursor"],)
        codex_adapter = mock.Mock()
        codex_adapter.discover_legacy.return_value = (rows["codex"],)
        claude_adapter = mock.Mock()
        claude_adapter.discover_legacy.return_value = (rows["claude"],)
        kiro_adapter = mock.Mock()
        kiro_adapter.discover_legacy.return_value = (rows["kiro"],)
        pi_adapter = mock.Mock()
        pi_adapter.discover_legacy.return_value = (rows["pi"],)
        hermes_adapter = mock.Mock()
        hermes_adapter.discover_legacy.return_value = (rows["hermes"],)
        grok_adapter = mock.Mock()
        grok_adapter.discover_legacy.return_value = (rows["grok"],)
        with mock.patch.object(
            meter, "_claude_native_adapter", return_value=claude_adapter
        ), mock.patch.object(
            meter, "codex_session_sources", return_value=[rows["codex"]]
        ), mock.patch.object(
            meter, "_codex_native_adapter", return_value=codex_adapter
        ), mock.patch.object(
            meter, "cursor_session_sources", return_value=[rows["cursor"]]
        ), mock.patch.object(
            meter, "_cursor_native_adapter", return_value=cursor_adapter
        ), mock.patch.object(
            meter, "_opencode_native_adapter", return_value=opencode_adapter
        ), mock.patch.object(
            meter, "_kiro_native_adapter", return_value=kiro_adapter
        ), mock.patch.object(
            meter, "_pi_native_adapter", return_value=pi_adapter
        ), mock.patch.object(
            meter, "_hermes_native_adapter", return_value=hermes_adapter
        ), mock.patch.object(
            meter, "_grok_native_adapter", return_value=grok_adapter
        ):
            discovered = meter.all_session_sources()

        self.assertEqual(discovered, [
            rows["claude"], rows["codex"], rows["cursor"], rows["opencode"], rows["kiro"],
            rows["pi"], rows["hermes"], rows["grok"],
        ])
        claude_adapter.discover_legacy.assert_called_once()
        codex_adapter.discover_legacy.assert_called_once()
        cursor_adapter.discover_legacy.assert_called_once()
        opencode_adapter.discover_legacy.assert_called_once()
        kiro_adapter.discover_legacy.assert_called_once()
        pi_adapter.discover_legacy.assert_called_once()
        hermes_adapter.discover_legacy.assert_called_once()
        grok_adapter.discover_legacy.assert_called_once()

    def test_one_discovery_failure_returns_other_runtimes_and_bounded_status(self):
        codex_source = {"provider": "codex", "id": "codex-session"}
        opencode_adapter = mock.Mock()
        opencode_adapter.discover_legacy.return_value = ()
        cursor_adapter = mock.Mock()
        cursor_adapter.discover_legacy.return_value = ()
        codex_adapter = mock.Mock()
        codex_adapter.discover_legacy.return_value = (codex_source,)
        claude_adapter = mock.Mock()
        claude_adapter.discover_legacy.side_effect = RuntimeError("/private/sentinel")
        kiro_adapter = mock.Mock()
        kiro_adapter.discover_legacy.return_value = ()
        pi_adapter = mock.Mock()
        pi_adapter.discover_legacy.return_value = ()
        hermes_adapter = mock.Mock()
        hermes_adapter.discover_legacy.return_value = ()
        grok_adapter = mock.Mock()
        grok_adapter.discover_legacy.return_value = ()
        with mock.patch.object(
            meter, "_claude_native_adapter", return_value=claude_adapter
        ), mock.patch.object(
            meter, "_codex_native_adapter", return_value=codex_adapter
        ), mock.patch.object(
            meter, "_cursor_native_adapter", return_value=cursor_adapter
        ), mock.patch.object(
            meter, "_opencode_native_adapter", return_value=opencode_adapter
        ), mock.patch.object(
            meter, "_kiro_native_adapter", return_value=kiro_adapter
        ), mock.patch.object(
            meter, "_pi_native_adapter", return_value=pi_adapter
        ), mock.patch.object(
            meter, "_hermes_native_adapter", return_value=hermes_adapter
        ), mock.patch.object(
            meter, "_grok_native_adapter", return_value=grok_adapter
        ):
            discovered = meter.all_session_sources()

        self.assertEqual(discovered, [codex_source])
        self.assertEqual(meter.runtime_discovery_failures(), [{
            "runtime_id": "claude",
            "operation": "discover",
            "code": "adapter_failed",
        }])


class RuntimePackageStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]

    def test_macos_installer_requires_and_stages_internal_package(self):
        script = (self.root / "scripts" / "install").read_text()

        self.assertIn('RUNTIME_MANIFEST="$SOURCE_ROOT/runtime-manifest.txt"', script)
        self.assertIn("python3 -m token_meter.packaging manifest", script)
        self.assertIn("python3 -m token_meter.packaging parity", script)
        self.assertIn(
            'find "$source_path" -type f -name \'*.py\' -print',
            script,
        )
        self.assertIn(
            'ditto "$package_file" "$INSTALL_ROOT/$package_relative"',
            script,
        )

    def test_linux_installer_requires_and_stages_internal_package(self):
        script = (self.root / "scripts" / "install-linux").read_text()

        self.assertIn('RUNTIME_MANIFEST="$SOURCE_ROOT/runtime-manifest.txt"', script)
        self.assertIn("python3 -m token_meter.packaging manifest", script)
        self.assertIn("python3 -m token_meter.packaging parity", script)
        self.assertIn(
            'find "$source_path" -type f -name \'*.py\' -print',
            script,
        )
        self.assertIn(
            'cp -p "$package_file" "$INSTALL_ROOT/$package_relative"',
            script,
        )


if __name__ == "__main__":
    unittest.main()
