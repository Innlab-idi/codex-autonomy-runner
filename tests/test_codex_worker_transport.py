"""Offline tests for the Windows transport; no Codex or Windows guard is invoked."""
from pathlib import Path
import subprocess
import unittest

from codex_autonomy_runner.codex_worker_executor import CodexLaunchPlan
from codex_autonomy_runner.codex_worker_transport import (
    CODEX_CLI_VERSION, ContainmentPreflightResult, ContainmentPreflightStatus,
    WindowsCodexProcessTransport,
)


class FakePreflight:
    def __init__(self, result=ContainmentPreflightResult(ContainmentPreflightStatus.SATISFIED)):
        self.result, self.calls = result, []
    def verify(self, plan):
        self.calls.append(plan)
        return self.result


class FakeProcess:
    def __init__(self, returncode=0, failure=None):
        self.returncode, self.failure, self.inputs, self.pid = returncode, failure, [], 123
    def communicate(self, *, input, timeout):
        self.inputs.append((input, timeout))
        if self.failure:
            raise self.failure
        return b"", b""


class FakeProcessApi:
    def __init__(self, *, version=CODEX_CLI_VERSION, head="a" * 40, process=None,
                 launch_failure=None, cleanup=True):
        self.version, self.head, self.process = version, head, process or FakeProcess()
        self.launch_failure, self.cleanup = launch_failure, cleanup
        self.version_calls, self.head_calls, self.launches, self.cleanups = [], [], [], []
    def observe_version(self, executable, cwd, environment):
        self.version_calls.append((executable, cwd, dict(environment)))
        return self.version
    def observe_head(self, repository, environment):
        self.head_calls.append((repository, dict(environment)))
        return self.head
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


class WindowsCodexProcessTransportTests(unittest.TestCase):
    def setUp(self):
        self.prompt = "private prompt"
        self.plan = CodexLaunchPlan(Path.cwd().resolve(), "CHECKPOINT", "a" * 40,
            ("allowed.txt",), "123e4567-e89b-12d3-a456-426614174000", "profile",
            (r"C:\\Tools\\codex.exe", "exec", "-"),
            (("PATH", r"C:\\Tools"), ("PYTHONNOUSERSITE", "1")), self.prompt.encode())
        self.preflight, self.api = FakePreflight(), FakeProcessApi()

    def transport(self, **changes):
        options = dict(timeout_seconds=12, process_api=self.api, platform_name="win32")
        options.update(changes)
        return WindowsCodexProcessTransport(self.preflight, **options)

    def test_positive_preflight_launches_once_with_exact_plan_inputs(self):
        result = self.transport().execute(self.plan)
        self.assertEqual((True, True, 0, False, True),
                         (result.terminated, result.completion_reliable, result.returncode,
                          result.technical_failure, result.cleanup_confirmed))
        self.assertEqual(1, len(self.preflight.calls)); self.assertEqual(1, len(self.api.launches))
        argv, cwd, environment = self.api.launches[0]
        self.assertEqual(self.plan.argv, argv); self.assertEqual(self.plan.repository, cwd)
        self.assertEqual(dict(self.plan.environment), environment)
        self.assertEqual([(self.plan.stdin_payload, 12)], self.api.process.inputs)
        self.assertNotIn(self.prompt, repr(result)); self.assertNotIn(self.prompt, repr(self.plan))

    def test_nonzero_exits_are_reliable_and_not_retried(self):
        for code in (1, 2):
            with self.subTest(code=code):
                self.api.process = FakeProcess(code)
                result = self.transport().execute(self.plan)
                self.assertEqual(code, result.returncode); self.assertTrue(result.completion_reliable)
                self.assertEqual(1, len(self.api.launches))
                self.api.launches.clear()

    def test_refused_unavailable_or_bad_runtime_preflight_never_launches(self):
        for status in (ContainmentPreflightStatus.REFUSED, ContainmentPreflightStatus.UNAVAILABLE):
            with self.subTest(status=status):
                self.preflight = FakePreflight(ContainmentPreflightResult(status))
                self.api = FakeProcessApi()
                self.assertTrue(self.transport().execute(self.plan).technical_failure)
                self.assertFalse(self.api.launches)
        for changes in ({"head": "b" * 40}, {"version": "wrong"}):
            with self.subTest(changes=changes):
                self.preflight, self.api = FakePreflight(), FakeProcessApi(**changes)
                self.assertTrue(self.transport().execute(self.plan).technical_failure)
                self.assertFalse(self.api.launches)

    def test_platform_malformed_preflight_and_launch_failure_fail_closed(self):
        self.assertFalse(self.transport(platform_name="linux").execute(self.plan).completion_reliable)
        self.assertFalse(self.api.launches)
        self.preflight = type("Bad", (), {"verify": lambda self, plan: object()})()
        self.assertFalse(self.transport().execute(self.plan).completion_reliable)
        self.preflight = FakePreflight()
        self.api = FakeProcessApi(launch_failure=OSError("secret"))
        result = self.transport().execute(self.plan)
        self.assertTrue(result.technical_failure); self.assertEqual(1, len(self.api.launches))
        self.assertNotIn("secret", repr(result))

    def test_timeout_and_interruption_cleanup_once_without_retry(self):
        self.api.process = FakeProcess(failure=subprocess.TimeoutExpired("codex", 12))
        result = self.transport().execute(self.plan)
        self.assertTrue(result.completion_reliable); self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(1, len(self.api.cleanups)); self.assertEqual(1, len(self.api.launches))
        self.api = FakeProcessApi(process=FakeProcess(failure=KeyboardInterrupt()))
        result = self.transport().execute(self.plan)
        self.assertTrue(result.completion_reliable); self.assertTrue(result.cleanup_confirmed)
        self.assertEqual(1, len(self.api.cleanups)); self.assertEqual(1, len(self.api.launches))

    def test_cleanup_failure_and_invalid_timeout_fail_closed(self):
        self.api.process = FakeProcess(failure=subprocess.TimeoutExpired("codex", 12))
        self.api.cleanup = False
        result = self.transport().execute(self.plan)
        self.assertFalse(result.completion_reliable); self.assertFalse(result.cleanup_confirmed)
        self.assertEqual(1, len(self.api.cleanups))
        for timeout in (0, -1, True, "bad"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError): self.transport(timeout_seconds=timeout)

    def test_import_source_has_no_shell_or_prompt_file_route(self):
        source = Path(__file__).parents[1].joinpath("codex_autonomy_runner", "codex_worker_transport.py").read_text(encoding="utf-8")
        for forbidden in ("os.system", "Start-Process", "shell=True", ".open(", "write_text", "git push", '("gh",'):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
