import hashlib
import io
import json
import os
import plistlib
import tarfile
import tempfile
import unittest
from unittest import mock

from token_meter.services import work_setup as S


class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Run:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


class WorkSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = {"model": "token-meter-gemma", "ollama_url": "http://127.0.0.1:11434"}
        self.urls, self.commands = [], []
        self.ollama = {}  # url -> {"version":..., "models": [...]}
        self.files = {}

    def opener(self, request, timeout=0):
        url = request.full_url
        self.urls.append(url)
        for base, info in self.ollama.items():
            if url == base + "/api/version":
                return FakeResponse(json.dumps({"version": info["version"]}).encode())
            if url == base + "/api/tags":
                return FakeResponse(json.dumps({"models": [{"name": n} for n in info["models"]]}).encode())
        if url in self.files:
            return FakeResponse(self.files[url])
        raise OSError("unreachable")

    def runner(self, command, **kwargs):
        self.commands.append(command)
        if command[0] == "/usr/bin/codesign":
            return Run(0, f"TeamIdentifier={S.OLLAMA_TEAM_ID}\n")
        if command[:2] == ["/bin/launchctl", "bootstrap"]:
            self.ollama[S.MANAGED_URL] = {"version": S.OLLAMA_VERSION, "models": []}
        if len(command) > 1 and command[1] == "create":
            target = "http://" + kwargs["env"]["OLLAMA_HOST"]
            self.ollama[target]["models"].append(command[2] + ":latest")
        if len(command) > 1 and command[1] == "rm":
            target = "http://" + kwargs["env"]["OLLAMA_HOST"]
            self.ollama[target]["models"].remove(command[2] + ":latest")
        return Run()

    def make(self, **kwargs):
        def set_url(url):
            self.settings["ollama_url"] = url
        return S.WorkSetup(base_dir=os.path.join(self.tmp.name, "ollama"), cache_dir=os.path.join(self.tmp.name, "cache"),
                           launch_agents_dir=os.path.join(self.tmp.name, "agents"), get_settings=lambda: dict(self.settings),
                           set_ollama_url=set_url, opener=self.opener, runner=self.runner, uid=501,
                           sleep=lambda s: None, disk_free=kwargs.pop("disk_free", lambda p: 10 ** 13),
                           total_memory=kwargs.pop("total_memory", lambda: 32 * 1024 ** 3), **kwargs)

    def small_model(self):
        files = []
        for name in (S.MODEL_GGUF,):
            data = name.encode() * 3
            files.append((name, len(data), "sha256:" + hashlib.sha256(data).hexdigest()))
            self.files[f"{S.MODEL_BASE_URL}/{name}"] = data
        return mock.patch.object(S, "MODEL_FILES", tuple(files))

    def ollama_archive(self, extra=None):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            for name, data in [("ollama", b"\xcf\xfa\xed\xfe" + b"x" * 20), *(extra or [])]:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
        data = buffer.getvalue()
        self.files[S.OLLAMA_ARCHIVE_URL] = data
        return mock.patch.multiple(S, OLLAMA_ARCHIVE_SIZE=len(data),
                                   OLLAMA_ARCHIVE_DIGEST="sha256:" + hashlib.sha256(data).hexdigest())

    def test_existing_ollama_with_the_model_needs_nothing(self):
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": ["token-meter-gemma:latest"]}
        ready = mock.Mock()
        with mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make(on_ready=ready)
            setup.run()
        self.assertEqual(setup.status()["state"], "ready")
        ready.assert_called_once()
        self.assertFalse(any("huggingface" in u or "github" in u for u in self.urls))

    def runner_path(self):
        path = os.path.join(self.tmp.name, "ollama-cli")
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def test_missing_ollama_installs_the_pinned_build_and_imports_the_model(self):
        with self.ollama_archive(), self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            setup.run()
        self.assertEqual(self.settings["ollama_url"], S.MANAGED_URL)
        self.assertTrue(os.path.isfile(setup.binary))
        with open(setup.plist_path, "rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["ProgramArguments"], [setup.binary, "serve"])
        self.assertEqual(plist["EnvironmentVariables"]["OLLAMA_HOST"], "127.0.0.1:11435")
        self.assertEqual(plist["StandardErrorPath"], "/dev/null")
        create = next(c for c in self.commands if len(c) > 1 and c[1] == "create")
        self.assertEqual(create[:4], [setup.binary, "create", "token-meter-gemma", "-f"])
        self.assertIn("token-meter-gemma:latest", self.ollama[S.MANAGED_URL]["models"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "cache", f"gemma-4-e4b-qat-{S.MODEL_COMMIT[:12]}")))
        self.assertTrue(all(not c[0].endswith("sudo") for c in self.commands))

    def test_tampered_download_is_rejected_and_removed(self):
        with self.ollama_archive(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            self.files[S.OLLAMA_ARCHIVE_URL] = b"y" * S.OLLAMA_ARCHIVE_SIZE
            setup = self.make()
            with self.assertRaises(S.SetupError) as caught:
                setup.run()
        self.assertEqual(caught.exception.reason, "verify")
        self.assertFalse(os.path.exists(setup.binary))
        self.assertFalse(os.listdir(os.path.join(self.tmp.name, "cache")))

    def test_unsigned_binary_is_rejected(self):
        def runner(command, **kwargs):
            if command[0] == "/usr/bin/codesign":
                return Run(0, "TeamIdentifier=SOMEONEELSE\n")
            return self.runner(command, **kwargs)
        with self.ollama_archive(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            setup.runner = runner
            with self.assertRaises(S.SetupError) as caught:
                setup.run()
        self.assertEqual(caught.exception.reason, "verify")
        self.assertFalse(os.path.exists(setup.binary))

    def test_archive_paths_outside_the_folder_are_refused(self):
        with self.ollama_archive(extra=[("../escape", b"x")]), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            with self.assertRaises(S.SetupError):
                setup.run()
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "ollama", "escape")))

    def test_low_disk_space_stops_before_downloading(self):
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": []}
        with self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make(disk_free=lambda p: 1)
            setup._run_safely()
        status = setup.status()
        self.assertEqual((status["state"], status["reason"]), ("failed", "disk_space"))
        self.assertGreater(status["needed_bytes"], 0)
        self.assertFalse(any("huggingface" in u for u in self.urls))

    def test_old_ollama_is_replaced_by_the_managed_runtime(self):
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.20.1", "models": []}
        with self.ollama_archive(), self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            self.make().run()
        self.assertEqual(self.settings["ollama_url"], S.MANAGED_URL)

    def test_stop_agent_unloads_and_removes_the_launch_agent(self):
        with self.ollama_archive(), self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            setup.run()
        self.assertTrue(setup.stop_agent())
        self.assertFalse(os.path.exists(setup.plist_path))
        self.assertIn(["/bin/launchctl", "bootout", f"gui/501/{S.AGENT_LABEL}"], self.commands)
        self.assertFalse(setup.stop_agent())

    def test_failures_report_only_a_reason_code(self):
        setup = self.make()
        setup.get_settings = mock.Mock(side_effect=RuntimeError("/Users/someone/secret path"))
        setup._run_safely()
        status = setup._state
        self.assertEqual((status["state"], status["reason"]), ("failed", "internal"))
        self.assertNotIn("secret", json.dumps(status))

    def test_turning_off_mid_download_cancels_without_starting_anything(self):
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": []}
        with self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make()
            original = setup._download

            def download(*args):
                setup._cancel.set()
                return original(*args)
            setup._download = download
            setup._run_safely()
        self.assertEqual(setup.status()["state"], "idle")
        self.assertFalse(any(len(c) > 1 and c[1] == "create" for c in self.commands))

    def test_cancel_before_the_agent_starts_writes_no_login_item(self):
        with self.ollama_archive(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            original = setup._install_ollama

            def install():
                path = original()
                setup._cancel.set()
                return path
            setup._install_ollama = install
            setup._run_safely()
        self.assertFalse(os.path.exists(setup.plist_path))
        self.assertFalse(any(c[:2] == ["/bin/launchctl", "bootstrap"] for c in self.commands))

    def test_installed_but_stopped_ollama_is_waited_for_not_replaced(self):
        cli = self.runner_path()
        calls = {"n": 0}

        def sleep(_seconds):
            calls["n"] += 1
            if calls["n"] == 3:
                self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": ["token-meter-gemma:latest"]}
        with mock.patch.object(S, "CLI_CANDIDATES", (cli,)):
            setup = self.make()
            setup.sleep = sleep
            setup.run()
        self.assertEqual(self.settings["ollama_url"], "http://127.0.0.1:11434")
        self.assertFalse(any("github" in u for u in self.urls))

    def test_installed_ollama_that_never_starts_reports_offline(self):
        with mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make()
            setup._run_safely()
        self.assertEqual((setup.status()["state"], setup.status()["reason"]), ("failed", "ollama_offline"))
        self.assertEqual(self.settings["ollama_url"], "http://127.0.0.1:11434")

    def test_unanswered_model_list_does_not_start_a_download(self):
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": []}
        opener = self.opener

        def flaky(request, timeout=0):
            if request.full_url.endswith("/api/tags"):
                raise OSError("timed out")
            return opener(request, timeout)
        with self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make()
            setup.opener = setup.loopback_opener = flaky
            setup._run_safely()
        self.assertEqual(setup.status()["reason"], "ollama_offline")
        self.assertFalse(any("huggingface" in u for u in self.urls))

    def test_port_conflict_unloads_the_agent(self):
        def runner(command, **kwargs):
            if command[:2] == ["/bin/launchctl", "bootstrap"]:
                return Run()  # Loaded, but something else holds the port and Ollama never answers.
            return self.runner(command, **kwargs)
        with self.ollama_archive(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            setup.runner = runner
            setup._run_safely()
        self.assertEqual(setup.status()["reason"], "ollama_start")
        self.assertFalse(os.path.exists(setup.plist_path))

    def test_start_reports_checking_immediately(self):
        setup = self.make()
        release = __import__("threading").Event()
        setup.run = lambda: release.wait(5)
        self.assertTrue(setup.start())
        self.assertEqual(setup.status()["state"], "checking")
        self.assertFalse(setup.start())
        release.set()

    def test_loopback_probes_ignore_http_proxies(self):
        with mock.patch.dict(os.environ, {"http_proxy": "http://proxy.example:3128"}):
            self.assertTrue(S.urllib.request.getproxies().get("http"))
            self.assertFalse(any(isinstance(h, S.urllib.request.ProxyHandler) and h.proxies
                                 for h in S._LOOPBACK_OPENER.handlers))
            self.assertTrue(any(isinstance(h, S.urllib.request.ProxyHandler) and h.proxies
                                for h in S.urllib.request.build_opener().handlers))

    def test_extraction_refuses_chained_or_outside_symlinks_and_masks_modes(self):
        for entries in ([("link", "sym", "../outside")], [("a", "sym", "b"), ("b", "sym", "x/../.."), ],
                        [("dir/link", "sym", "../../x")], [("dev", "chr", "")], [("hard", "lnk", "ollama")]):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
                for name, kind, target in entries:
                    info = tarfile.TarInfo(name)
                    info.type = {"sym": tarfile.SYMTYPE, "chr": tarfile.CHRTYPE, "lnk": tarfile.LNKTYPE}[kind]
                    info.linkname = target
                    bundle.addfile(info)
            archive = os.path.join(self.tmp.name, "bad.tgz")
            with open(archive, "wb") as handle:
                handle.write(buffer.getvalue())
            out = tempfile.mkdtemp(dir=self.tmp.name)
            with self.assertRaises(S.SetupError, msg=entries):
                S.safe_extract(archive, out)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            info = tarfile.TarInfo("bin/tool")
            info.size, info.mode = 3, 0o4755
            bundle.addfile(info, io.BytesIO(b"abc"))
            alias = tarfile.TarInfo("bin/alias")
            alias.type, alias.linkname = tarfile.SYMTYPE, "tool"
            bundle.addfile(alias)
        archive = os.path.join(self.tmp.name, "ok.tgz")
        with open(archive, "wb") as handle:
            handle.write(buffer.getvalue())
        out = tempfile.mkdtemp(dir=self.tmp.name)
        S.safe_extract(archive, out)
        self.assertEqual(os.stat(os.path.join(out, "bin", "tool")).st_mode & 0o7777, 0o755)
        self.assertEqual(os.readlink(os.path.join(out, "bin", "alias")), "tool")

    def test_nested_unsigned_library_is_rejected(self):
        def runner(command, **kwargs):
            if command[0] == "/usr/bin/codesign" and command[-1].endswith("nested.dylib"):
                return Run(0, "TeamIdentifier=OTHER\n")
            return self.runner(command, **kwargs)
        with self.ollama_archive(extra=[("lib/nested.dylib", b"\xcf\xfa\xed\xfe" + b"y" * 8)]), \
                mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            setup.runner = runner
            with self.assertRaises(S.SetupError):
                setup.run()
        self.assertFalse(os.path.exists(setup.binary))

    def test_turning_back_on_during_a_cancel_restarts_setup(self):
        import threading
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": ["token-meter-gemma:latest"]}
        gate, runs = threading.Event(), []
        with mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make()
            original = setup.run

            def run():
                runs.append(1)
                if len(runs) == 1:
                    gate.wait(5)
                    setup._checkpoint()
                original()
            setup.run = run
            self.assertTrue(setup.start())
            setup._cancel.set()
            self.assertTrue(setup.start())  # Re-enabled while the old run is still winding down.
            gate.set()
            setup._thread.join(5)
        self.assertEqual(len(runs), 2)
        self.assertEqual(setup.status()["state"], "ready")

    def off_on_off(self):
        import threading
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4", "models": ["token-meter-gemma:latest"]}
        gate, runs = threading.Event(), []
        with mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            setup = self.make()
            original = setup.run

            def run():
                runs.append(1)
                if len(runs) == 1:
                    gate.wait(5)
                    setup._checkpoint()
                original()
            setup.run = run
            setup.start()
            setup.cancel(wait_s=0)
            setup.start()
            return setup, gate, runs

    def test_off_on_off_during_a_cancel_does_not_run_setup_again(self):
        setup, gate, runs = self.off_on_off()
        setup.cancel(wait_s=0)
        gate.set()
        setup._thread.join(5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(setup.status()["state"], "idle")

    def test_restart_is_skipped_when_work_insights_were_turned_off(self):
        setup, gate, runs = self.off_on_off()
        self.settings["enabled"] = False
        gate.set()
        setup._thread.join(5)
        self.assertEqual(len(runs), 1)

    def test_start_after_the_old_run_passed_its_restart_check_starts_a_new_run(self):
        setup, ran = self.make(), []
        old = mock.Mock(is_alive=lambda: True)
        setup._thread, setup._winding_down = old, True
        setup.run = lambda: ran.append(1)
        self.assertTrue(setup.start())
        self.assertIsNot(setup._thread, old)
        setup._thread.join(5)
        self.assertEqual(ran, [1])

    def test_ollama_in_the_users_applications_folder_is_found(self):
        home = os.path.join(self.tmp.name, "home")
        app = os.path.join(home, "Applications", "Ollama.app", "Contents", "Resources")
        os.makedirs(app)
        with open(os.path.join(app, "ollama"), "w") as handle:
            handle.write("#!/bin/sh\n")
        os.chmod(os.path.join(app, "ollama"), 0o755)
        self.assertIn("~/Applications/Ollama.app/Contents/Resources/ollama", S.CLI_CANDIDATES)
        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(S, "CLI_CANDIDATES", ("~/Applications/Ollama.app/Contents/Resources/ollama",)):
            self.assertEqual(self.make()._find_cli(), os.path.join(app, "ollama"))

    def test_a_mac_too_small_for_the_model_downloads_nothing(self):
        setup = self.make(total_memory=lambda: 8 * 1024 ** 3)
        setup._run_once()
        self.assertEqual((setup.status()["state"], setup.status()["reason"]), (S.FAILED, "memory"))
        self.assertEqual(self.urls, [])
        self.assertEqual(self.commands, [])
        self.assertEqual(S.MIN_TOTAL_MEMORY_BYTES, 10 * 1024 ** 3)

    def test_pinned_sources_and_versions(self):
        self.assertTrue(S.OLLAMA_ARCHIVE_URL.startswith("https://github.com/ollama/ollama/releases/download/v0.34.4/"))
        self.assertEqual(S.MODEL_BASE_URL, "https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf/resolve/"
                                           "4b4a2c1d584be7264f87aac328a1bc739ce81b6c")
        self.assertEqual(S.MODEL_FILES, (("gemma-4-E4B_q4_0-it.gguf", 5154941280,
                                          "sha256:676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee"),))
        self.assertEqual(S.MANAGED_URL, "http://127.0.0.1:11435")
        self.assertEqual(S.RETIRED_MODELS, ("token-meter-jet", "token-meter-winnow"))

    def test_gguf_is_imported_as_is_and_the_retired_model_leaves_the_managed_ollama(self):
        with self.ollama_archive(), self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            os.makedirs(os.path.join(self.tmp.name, "cache", "jet-v6.2.0"))
            os.makedirs(os.path.join(self.tmp.name, "cache", "winnow-e4b-aabbd52f5dfc", "gguf"))
            original = self.runner

            def runner(command, **kwargs):
                result = original(command, **kwargs)
                if command[:2] == ["/bin/launchctl", "bootstrap"]:
                    self.ollama[S.MANAGED_URL]["models"] += ["token-meter-jet:latest", "token-meter-winnow:latest"]
                if len(command) > 1 and command[1] == "create":
                    with open(command[-1], encoding="utf-8") as handle:
                        self.modelfile = handle.read()
                return result

            setup.runner = runner
            setup.run()
        self.assertTrue(self.modelfile.startswith(
            "FROM " + os.path.join(self.tmp.name, "cache", f"gemma-4-e4b-qat-{S.MODEL_COMMIT[:12]}", S.MODEL_GGUF) + "\n"))
        self.assertEqual(self.ollama[S.MANAGED_URL]["models"], ["token-meter-gemma:latest"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "cache", "jet-v6.2.0")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "cache", "winnow-e4b-aabbd52f5dfc")))

    def test_retired_models_leave_the_managed_ollama_even_when_the_model_is_already_there(self):
        with self.ollama_archive(), mock.patch.object(S, "CLI_CANDIDATES", ()):
            setup = self.make()
            original = self.runner

            def runner(command, **kwargs):
                result = original(command, **kwargs)
                if command[:2] == ["/bin/launchctl", "bootstrap"]:
                    self.ollama[S.MANAGED_URL]["models"] += ["token-meter-gemma:latest", "token-meter-winnow:latest",
                                                             "token-meter-jet:latest"]
                return result

            setup.runner = runner
            setup.run()
        self.assertEqual(self.ollama[S.MANAGED_URL]["models"], ["token-meter-gemma:latest"])
        self.assertFalse(any("huggingface" in u for u in self.urls))

    def test_modelfile_keeps_the_license(self):
        with self.small_model():
            folder = self.make()._download_model()
        with open(os.path.join(folder, "Modelfile"), encoding="utf-8") as handle:
            modelfile = handle.read()
        self.assertIn(f'LICENSE """{S.MODEL_LICENSE}"""', modelfile)
        self.assertIn("Apache License, Version 2.0", S.MODEL_LICENSE)
        self.assertEqual(modelfile.count('"""'), 2)

    def test_a_users_own_ollama_keeps_the_retired_model(self):
        self.ollama["http://127.0.0.1:11434"] = {"version": "0.34.4",
                                                 "models": ["token-meter-jet:latest", "token-meter-winnow:latest"]}
        with self.small_model(), mock.patch.object(S, "CLI_CANDIDATES", (self.runner_path(),)):
            self.make().run()
        self.assertEqual(sorted(self.ollama["http://127.0.0.1:11434"]["models"]),
                         ["token-meter-gemma:latest", "token-meter-jet:latest", "token-meter-winnow:latest"])
        self.assertFalse(any(len(c) > 1 and c[1] == "rm" for c in self.commands))


class UninstallCleanupTests(unittest.TestCase):
    def test_uninstall_removes_partial_model_downloads(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as home:
            cache = os.path.join(home, ".cache", "token-meter")
            for name in ("gemma-4-e4b-qat-4b4a2c1d584b", "winnow-e4b-aabbd52f5dfc", "jet-v6.2.0"):
                os.makedirs(os.path.join(cache, name, "gguf"))
            open(os.path.join(cache, "ollama-0.34.4-darwin.tgz"), "w").close()
            keep = os.path.join(cache, "unrelated.txt")
            open(keep, "w").close()
            bin_dir = os.path.join(home, "bin")
            os.makedirs(bin_dir)
            with open(os.path.join(bin_dir, "launchctl"), "w") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(os.path.join(bin_dir, "launchctl"), 0o755)
            env = dict(os.environ, HOME=home, PATH=bin_dir + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", os.path.join(root, "scripts", "uninstall-launch-agent")],
                                    capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(os.listdir(cache)), ["unrelated.txt"])
            self.assertIn("downloads Token Meter set up for Work insights", result.stdout)


if __name__ == "__main__":
    unittest.main()
