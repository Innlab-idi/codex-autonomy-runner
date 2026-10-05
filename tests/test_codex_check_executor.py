"""Offline tests for the contained CheckExecutor; never run Codex or a real check."""
import json
from pathlib import Path, PureWindowsPath
import subprocess
import sys
from types import SimpleNamespace
import unittest

from codex_autonomy_runner.codex_check_executor import (
    CODEX_CLI_VERSION, CheckContainmentPreflightResult, CheckContainmentPreflightStatus,
    WindowsCodexCheckExecutor, _SCRUBBER_MARKER, _SCRUBBER_PROTOCOL_MAX_BYTES,
    _run_environment_scrubber,
)
from codex_autonomy_runner.runtime_worker import CheckCompletion, CheckExecutionContext, CheckKind


class FakePreflight:
    def __init__(self, result=None):
        self.result, self.calls = result, []

    def verify(self, plan):
        self.calls.append(plan)
        if self.result is None:
            return CheckContainmentPreflightResult(
                CheckContainmentPreflightStatus.SATISFIED,
                plan.attempt_id, plan.profile_id, plan.check_id,
            )
        return self.result(plan) if callable(self.result) else self.result


def check_protocol(returncode):
    return json.dumps({"status": "check", "returncode": returncode},
                      separators=(",", ":")).encode("ascii") + b"\n"


class FakeProcess:
    def __init__(self, returncode=0, failure=None, output=None):
        self.returncode, self.failure, self.timeouts, self.pid = returncode, failure, [], 123
        self.output = check_protocol(0) if output is None else output

    def communicate(self, *, timeout):
        self.timeouts.append(timeout)
        if self.failure:
            raise self.failure
        return self.output, None


class FakeProcessApi:
    def __init__(self, *, version=CODEX_CLI_VERSION, metadata=None, process=None,
                 launch_failure=None, cleanup=True):
        self.version, self.metadata, self.process = version, metadata, process or FakeProcess()
        self.launch_failure, self.cleanup = launch_failure, cleanup
        self.version_calls, self.metadata_calls, self.launches, self.cleanups = [], [], [], []

    def observe_version(self, executable, cwd, environment):
        self.version_calls.append((executable, cwd, dict(environment)))
        return self.version

    def observe_git_metadata(self, repository, environment):
        self.metadata_calls.append((repository, dict(environment)))
        return self.metadata or (repository / ".git", repository / "common-git")

    def launch(self, argv, cwd, environment):
        self.launches.append((argv, cwd, dict(environment)))
        if self.launch_failure:
            raise self.launch_failure
        return self.process

    def cleanup_tree(self, process):
        self.cleanups.append(process)
        if self.cleanup:
            process.returncode = -1
        return self.cleanup


class WindowsCodexCheckExecutorTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path.cwd().resolve()
        self.context = CheckExecutionContext(
            self.repo, "focused-check", CheckKind.FOCUSED,
            ("python", "-m", "unittest", "tests.test_csv_validation", "-v"), "attempt-1",
        )
        self.environment = {
            "SYSTEMROOT": r"C:\\Windows", "PATH": r"C:\\Tools", "USERPROFILE": r"C:\\Users\\worker",
            "APPDATA": r"C:\\Users\\worker\\AppData\\Roaming", "TEMP": r"C:\\Temp", "TMP": r"C:\\Temp",
            "GITHUB_TOKEN": "secret", "GH_TOKEN": "secret", "ARBITRARY_SECRET": "secret",
        }
        self.preflight, self.api = FakePreflight(), FakeProcessApi()

    def executor(self, **changes):
        options = dict(codex_path=r"C:\\Tools\\codex.exe", host_environment=self.environment,
                       timeout_seconds=12, process_api=self.api, platform_name="win32")
        options.update(changes)
        return WindowsCodexCheckExecutor(self.preflight, **options)

    def test_success_launches_exactly_once_with_exact_context_argv_and_cwd(self):
        result = self.executor().execute(self.context)
        self.assertEqual(CheckCompletion(0), result)
        self.assertEqual(1, len(self.preflight.calls)); self.assertEqual(1, len(self.api.launches))
        argv, cwd, environment = self.api.launches[0]
        self.assertEqual(self.repo, cwd)
        self.assertIn("sandbox", argv); self.assertNotIn("exec", argv)
        self.assertEqual(1, argv.count('windows.sandbox="elevated"'))
        self.assertLess(argv.index('windows.sandbox="elevated"'), argv.index("sandbox"))
        marker = argv.index(_SCRUBBER_MARKER)
        self.assertEqual(list(self.context.argv), json.loads(argv[marker + 2]))
        direct = argv[argv.index("--") + 1:]
        self.assertEqual(sys.executable, direct[0])
        self.assertEqual(("-I", "-S", "-B"), direct[1:4])
        self.assertEqual(1, direct.count("-I")); self.assertEqual(1, direct.count("-S"))
        self.assertEqual(1, direct.count("-B"))
        self.assertEqual(12, self.api.process.timeouts[0])
        self.assertEqual(environment, dict(self.api.version_calls[0][2]))

    def test_nonzero_is_reliable_unsatisfied_without_retry(self):
        self.api.process = FakeProcess(output=check_protocol(7))
        result = self.executor().execute(self.context)
        self.assertEqual(CheckCompletion(7), result)
        self.assertFalse(result.technical_failure)
        self.assertEqual(1, len(self.api.launches)); self.assertEqual(0, len(self.api.cleanups))

    def test_wrapper_collision_returncodes_are_preserved_exactly(self):
        for returncode in (125, 126, 127):
            with self.subTest(returncode=returncode):
                self.api = FakeProcessApi(process=FakeProcess(output=check_protocol(returncode)))
                self.assertEqual(CheckCompletion(returncode), self.executor().execute(self.context))
                self.assertEqual(1, len(self.api.launches))

    def test_profile_restricts_filesystem_network_and_environment(self):
        self.executor().execute(self.context)
        plan = self.preflight.calls[0]
        argv, _, environment = self.api.launches[0]
        filesystem = next(value for value in argv if ".filesystem=" in value)
        network = next(value for value in argv if ".network.enabled=" in value)
        temporary = json.dumps(str(PureWindowsPath(self.environment["TEMP"])))
        credential = json.dumps(str(PureWindowsPath(self.environment["USERPROFILE"]) / ".git-credentials"))
        git_directory = json.dumps(str(PureWindowsPath(self.repo / ".git")))
        git_common = json.dumps(str(PureWindowsPath(self.repo / "common-git")))
        repository = json.dumps(str(PureWindowsPath(self.repo)))
        self.assertIn('"read"', filesystem); self.assertIn('"deny"', filesystem)
        self.assertIn(temporary + '="write"', filesystem)
        self.assertIn(credential + '="deny"', filesystem)
        self.assertIn(git_directory + '="read"', filesystem)
        self.assertIn(git_common + '="read"', filesystem)
        for metadata in (git_directory, git_common):
            self.assertNotIn(metadata + '="write"', filesystem)
            self.assertNotIn(metadata + '="deny"', filesystem)
        self.assertIn(repository + '="read"', filesystem)
        self.assertNotIn(repository + '="write"', filesystem)
        self.assertEqual(f"permissions.{plan.profile_id}.network.enabled=false", network)
        self.assertEqual("1", environment["PYTHONDONTWRITEBYTECODE"])
        self.assertEqual("1", environment["PYTHONNOUSERSITE"])
        for name in ("GITHUB_TOKEN", "GH_TOKEN", "ARBITRARY_SECRET", "CODEX_HOME"):
            self.assertNotIn(name, environment)

    def test_git_diff_check_context_preserves_inner_argv_with_read_only_metadata(self):
        context = CheckExecutionContext(
            self.repo, "publication-check", CheckKind.PUBLICATION, ("git", "diff", "--check"), "attempt-1",
        )
        result = self.executor().execute(context)
        self.assertEqual(CheckCompletion(0), result)
        self.assertEqual(1, len(self.preflight.calls)); self.assertEqual(1, len(self.api.launches))
        argv, cwd, _ = self.api.launches[0]
        filesystem = next(value for value in argv if ".filesystem=" in value)
        git_directory = json.dumps(str(PureWindowsPath(self.repo / ".git")))
        git_common = json.dumps(str(PureWindowsPath(self.repo / "common-git")))
        marker = argv.index(_SCRUBBER_MARKER)
        self.assertEqual(["git", "diff", "--check"], json.loads(argv[marker + 2]))
        self.assertEqual(self.repo, cwd)
        for metadata in (git_directory, git_common):
            self.assertIn(metadata + '="read"', filesystem)
            self.assertNotIn(metadata + '="write"', filesystem)
            self.assertNotIn(metadata + '="deny"', filesystem)

    def test_scrubber_replaces_inherited_environment_and_launches_exact_child(self):
        self.executor().execute(self.context)
        outer_argv = self.api.launches[0][0]
        marker = outer_argv.index(_SCRUBBER_MARKER)
        arguments = tuple(outer_argv[marker:marker + 3])
        expected_environment = json.loads(arguments[1])
        inherited = {
            "CONFIG_INJECTED_CANARY": "must-disappear",
            "PATH": "config-injected-replacement",
            "GITHUB_TOKEN": "must-disappear",
        }
        captured, protocol = {}, []

        def replace_environment(values):
            inherited.clear()
            inherited.update(values)

        def run_child(argv, **kwargs):
            captured["argv"], captured["kwargs"] = argv, kwargs
            return SimpleNamespace(returncode=7)

        result = _run_environment_scrubber(
            arguments, run_child=run_child, replace_environment=replace_environment,
            current_directory=lambda: str(self.repo), write_protocol=protocol.append,
        )
        self.assertEqual(0, result)
        self.assertEqual(self.context.argv, captured["argv"])
        self.assertEqual(str(self.repo), captured["kwargs"]["cwd"])
        self.assertEqual(expected_environment, captured["kwargs"]["env"])
        self.assertEqual(expected_environment, inherited)
        self.assertIs(captured["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertIs(captured["kwargs"]["stdout"], subprocess.DEVNULL)
        self.assertIs(captured["kwargs"]["stderr"], subprocess.DEVNULL)
        self.assertFalse(captured["kwargs"]["shell"]); self.assertFalse(captured["kwargs"]["check"])
        self.assertEqual('{"status":"check","returncode":7}\n', protocol[0])
        self.assertEqual(r"C:\\Tools", inherited["PATH"])
        for name in ("CONFIG_INJECTED_CANARY", "GITHUB_TOKEN", "GH_TOKEN",
                     "ARBITRARY_SECRET", "CODEX_HOME"):
            self.assertNotIn(name, inherited)

    def test_empty_later_argument_is_preserved_through_payload_and_scrubber(self):
        context = CheckExecutionContext(
            self.repo, "empty-argument", CheckKind.FOCUSED, ("tool", "", "value"), "attempt-1",
        )
        self.assertEqual(CheckCompletion(0), self.executor().execute(context))
        outer_argv = self.api.launches[0][0]
        marker = outer_argv.index(_SCRUBBER_MARKER)
        arguments = tuple(outer_argv[marker:marker + 3])
        self.assertEqual(["tool", "", "value"], json.loads(arguments[2]))
        captured, protocol = [], []

        def run_child(argv, **kwargs):
            captured.append((argv, kwargs))
            return SimpleNamespace(returncode=0)

        _run_environment_scrubber(
            arguments, run_child=run_child, replace_environment=lambda values: None,
            current_directory=lambda: str(self.repo), write_protocol=protocol.append,
        )
        self.assertEqual(("tool", "", "value"), captured[0][0])
        self.assertEqual('{"status":"check","returncode":0}\n', protocol[0])

    def test_empty_executable_and_nul_in_any_argument_are_rejected_before_launch(self):
        cases = (("", "argument"), ("to\0ol", "argument"), ("tool", "arg\0ument"))
        for argv in cases:
            with self.subTest(argv=argv):
                self.preflight, self.api = FakePreflight(), FakeProcessApi()
                context = CheckExecutionContext(
                    self.repo, "invalid-argv", CheckKind.FOCUSED, argv, "attempt-1",
                )
                result = self.executor().execute(context)
                self.assertTrue(result.technical_failure)
                self.assertFalse(self.preflight.calls); self.assertFalse(self.api.launches)

    def test_scrubber_validation_failure_emits_only_technical_protocol(self):
        protocol, launches = [], []
        result = _run_environment_scrubber(
            (_SCRUBBER_MARKER, '{"PATH":"x","PATH":"y"}', '["python"]'),
            run_child=lambda *args, **kwargs: launches.append((args, kwargs)),
            replace_environment=lambda values: None, current_directory=lambda: str(self.repo),
            write_protocol=protocol.append,
        )
        self.assertEqual(0, result); self.assertFalse(launches)
        self.assertEqual(['{"status":"technical_failure"}\n'], protocol)

    def test_protocol_refusals_are_fail_closed_without_retry(self):
        cases = (
            ("absent", b""),
            ("malformed", b"not-json\n"),
            ("too-long", b"x" * (_SCRUBBER_PROTOCOL_MAX_BYTES + 1)),
            ("extra-field", b'{"status":"check","returncode":0,"extra":1}\n'),
            ("wrong-returncode-type", b'{"status":"check","returncode":true}\n'),
            ("wrong-status-type", b'{"status":1,"returncode":0}\n'),
            ("multiple-records", check_protocol(0) + check_protocol(1)),
        )
        for name, output in cases:
            with self.subTest(name=name):
                self.api = FakeProcessApi(process=FakeProcess(output=output))
                result = self.executor().execute(self.context)
                self.assertTrue(result.technical_failure); self.assertFalse(result.completion_reliable)
                self.assertIsNone(result.returncode); self.assertEqual(1, len(self.api.launches))

    def test_valid_technical_protocol_and_outer_nonzero_are_distinct(self):
        technical = b'{"status":"technical_failure"}\n'
        self.api = FakeProcessApi(process=FakeProcess(output=technical))
        result = self.executor().execute(self.context)
        self.assertEqual(CheckCompletion(None, True, True), result)
        self.assertEqual(1, len(self.api.launches))
        self.api = FakeProcessApi(process=FakeProcess(returncode=1, output=check_protocol(0)))
        result = self.executor().execute(self.context)
        self.assertTrue(result.technical_failure); self.assertFalse(result.completion_reliable)
        self.assertIsNone(result.returncode); self.assertEqual(1, len(self.api.launches))

    def test_version_failures_never_launch(self):
        for version in (None, "codex-cli 0.156.2", "codex-cli 0.156.1\nextra"):
            with self.subTest(version=version):
                self.api = FakeProcessApi(version=version)
                result = self.executor().execute(self.context)
                self.assertTrue(result.technical_failure)
                self.assertFalse(self.api.launches)

    def test_stale_malformed_and_negative_preflight_never_launch(self):
        def bound(**changes):
            def result(plan):
                values = dict(attempt_id=plan.attempt_id, profile_id=plan.profile_id, check_id=plan.check_id)
                values.update(changes)
                return CheckContainmentPreflightResult(CheckContainmentPreflightStatus.SATISFIED, **values)
            return result
        cases = (
            bound(attempt_id="another-attempt"), bound(profile_id="another-profile"),
            bound(check_id="another-check"), bound(attempt_id=object()),
            lambda _plan: CheckContainmentPreflightResult(CheckContainmentPreflightStatus.REFUSED),
            lambda _plan: CheckContainmentPreflightResult(CheckContainmentPreflightStatus.UNAVAILABLE),
            lambda _plan: (_ for _ in ()).throw(RuntimeError("private containment fact")),
        )
        for result in cases:
            with self.subTest(result=result):
                self.preflight, self.api = FakePreflight(result), FakeProcessApi()
                completion = self.executor().execute(self.context)
                self.assertTrue(completion.technical_failure)
                self.assertFalse(self.api.launches)

    def test_lifecycle_failures_cleanup_once_without_retry(self):
        self.api = FakeProcessApi(launch_failure=OSError("secret launch failure"))
        result = self.executor().execute(self.context)
        self.assertTrue(result.technical_failure); self.assertTrue(result.completion_reliable)
        self.assertEqual(1, len(self.api.launches)); self.assertFalse(self.api.cleanups)
        for failure, cleanup in ((subprocess.TimeoutExpired("check", 12), True), (KeyboardInterrupt(), True),
                                 (SystemExit(), True), (RuntimeError("secret"), False)):
            with self.subTest(failure=type(failure).__name__, cleanup=cleanup):
                self.api = FakeProcessApi(process=FakeProcess(failure=failure), cleanup=cleanup)
                result = self.executor().execute(self.context)
                self.assertTrue(result.technical_failure); self.assertFalse(result.completion_reliable)
                self.assertEqual(1, len(self.api.launches)); self.assertEqual(1, len(self.api.cleanups))

    def test_invalid_timeout_platform_metadata_and_context_fail_closed(self):
        for timeout in (0, -1, True, False, "bad", float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError): self.executor(timeout_seconds=timeout)
        self.assertTrue(self.executor(platform_name="linux").execute(self.context).technical_failure)
        self.assertFalse(self.api.launches)
        self.api = FakeProcessApi(metadata=(self.repo / ".git", "not-a-path"))
        self.assertTrue(self.executor().execute(self.context).technical_failure)
        malformed = CheckExecutionContext(self.repo, "check", CheckKind.FOCUSED, ("python", 3), "attempt")
        self.api = FakeProcessApi()
        self.assertTrue(self.executor().execute(malformed).technical_failure)
        self.assertFalse(self.api.launches)
        overlapping = dict(self.environment, TEMP=str(self.repo), TMP=str(self.repo))
        self.api = FakeProcessApi()
        self.assertTrue(self.executor(host_environment=overlapping).execute(self.context).technical_failure)
        self.assertFalse(self.api.launches)

    def test_completion_is_sanitized(self):
        secret = "secret canary SID S-1-5-21 private stdout private stderr"
        self.preflight = FakePreflight(lambda _plan: (_ for _ in ()).throw(RuntimeError(secret)))
        result = self.executor().execute(self.context)
        self.assertTrue(result.technical_failure)
        for value in (secret, "SID", "private stdout", "private stderr", "RuntimeError"):
            self.assertNotIn(value, repr(result))
        self.assertFalse(self.api.launches)


if __name__ == "__main__":
    unittest.main()
