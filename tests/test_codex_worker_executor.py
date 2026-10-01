"""Offline tests for the injected Codex worker adapter; never run Codex."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from codex_autonomy_runner.codex_worker_executor import (
    CODEX_CLI_VERSION, CodexContainmentAttestation, CodexLaunchPlan,
    CodexProcessCompletion, CodexWorkerExecutionProfile, CodexWorkerExecutor,
    build_codex_launch_plan,
)
from codex_autonomy_runner.runtime_worker import WorkerCompletion, WorkerContext


class FakeTransport:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.plans = result, error, []

    def execute(self, plan):
        self.plans.append(plan)
        if self.error:
            raise self.error
        return self.result


class CodexWorkerExecutorTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path.cwd().resolve()
        self.secret = "confidential instruction \U0001f680"
        self.environment = {
            "USERPROFILE": r"C:\\Users\\worker", "APPDATA": r"C:\\Users\\worker\\AppData\\Roaming",
            "SYSTEMROOT": r"C:\\Windows", "PATH": r"C:\\Windows\\System32",
            "GITHUB_TOKEN": "not-copied", "PILOT_SECRET_CANARY": "not-copied", "UNKNOWN": "not-copied",
        }
        self.profile = CodexWorkerExecutionProfile(CodexContainmentAttestation("host-bound"))
        self.context = WorkerContext(self.repo, "WORKER-EXECUTOR-01A", "a" * 40,
                                     ("package/file.py",), "123e4567-e89b-12d3-a456-426614174000",
                                     self.secret, self.profile)

    def plan(self, **changes):
        options = dict(codex_path=r"C:\\Tools\\codex.exe", observed_codex_version=CODEX_CLI_VERSION,
                       host_environment=self.environment, execution_profile=self.profile)
        options.update(changes)
        return build_codex_launch_plan(self.context, **options)

    def test_profile_and_plan_are_frozen_and_bound(self):
        plan = self.plan()
        self.assertIsInstance(plan, CodexLaunchPlan)
        self.assertEqual(self.context.repository, plan.repository)
        self.assertEqual(self.context.checkpoint_id, plan.checkpoint_id)
        self.assertEqual(self.context.expected_head_sha, plan.expected_head_sha)
        self.assertEqual(self.context.permitted_paths, plan.permitted_paths)
        self.assertEqual(self.context.attempt_id, plan.attempt_id)
        with self.assertRaises(FrozenInstanceError):
            plan.profile_id = "other"
        with self.assertRaises(FrozenInstanceError):
            self.profile.network_enabled = True
        with self.assertRaises(ValueError):
            CodexWorkerExecutionProfile(CodexContainmentAttestation("x"), True)

    def test_context_and_version_validation_fail_closed(self):
        cases = (
            (WorkerContext(self.repo, "C", "A" * 40, ("a",), self.context.attempt_id, "x", self.profile), {}),
            (WorkerContext(self.repo, "C", "a" * 40, ("a",), "not-a-uuid", "x", self.profile), {}),
            (WorkerContext(self.repo, "C", "a" * 40, ("a",), self.context.attempt_id, "", self.profile), {}),
            (WorkerContext(self.repo, "C", "a" * 40, ("a",), self.context.attempt_id, 1, self.profile), {}),
            (WorkerContext(self.repo, "C", "a" * 40, ("a",), self.context.attempt_id, "x", object()), {}),
            (self.context, {"observed_codex_version": "0.156.2"}),
            (self.context, {"observed_codex_version": "0.156.1"}),
            (self.context, {"observed_codex_version": None}),
        )
        for context, options in cases:
            with self.subTest(options=options):
                original = self.context
                self.context = context
                with self.assertRaises(ValueError): self.plan(**options)
                self.context = original

    def test_home_environment_stdin_and_argv_contract(self):
        plan = self.plan()
        env = dict(plan.environment)
        self.assertEqual(b"confidential instruction \xf0\x9f\x9a\x80", plan.stdin_payload)
        self.assertEqual("-", plan.argv[-1])
        self.assertIn('windows.sandbox="elevated"', plan.argv)
        self.assertIn(f"permissions.{plan.profile_id}.network.enabled=false", plan.argv)
        self.assertEqual("1", env["PYTHONNOUSERSITE"])
        self.assertNotIn("CODEX_HOME", env)
        for name in ("GITHUB_TOKEN", "PILOT_SECRET_CANARY", "UNKNOWN"):
            self.assertNotIn(name, env)
        for value in (*plan.argv, *(value for _, value in plan.environment), repr(plan)):
            self.assertNotIn(self.secret, value)
        self.assertNotIn("--ask-for-approval", plan.argv)
        self.assertFalse(any("bypass" in value or "unsafe" in value or "shell" in value for value in plan.argv))
        self.assertIn('":minimal"="read"', next(v for v in plan.argv if ".filesystem=" in v))
        self.assertIn('":root"="read"', next(v for v in plan.argv if ".filesystem=" in v))
        table = next(v for v in plan.argv if ".filesystem=" in v)
        self.assertIn('"deny"', table)
        self.assertEqual(1, table.count('"write"'))
        accepted = dict(self.environment, CODEX_HOME=r"C:\\Users\\worker\\.codex")
        self.plan(host_environment=accepted)
        with self.assertRaises(ValueError): self.plan(host_environment=dict(self.environment, CODEX_HOME=r"C:\\custom"))

    def test_credential_overlap_is_rejected(self):
        overlapping = dict(self.environment, USERPROFILE=str(self.repo), APPDATA=str(self.repo / "AppData"))
        with self.assertRaises(ValueError): self.plan(host_environment=overlapping)

    def test_executor_maps_only_reliable_single_transport_completion_to_true(self):
        good = CodexProcessCompletion(True, True, 0)
        transport = FakeTransport(good)
        executor = CodexWorkerExecutor(transport, codex_path=r"C:\\Tools\\codex.exe", observed_codex_version=CODEX_CLI_VERSION,
                                       host_environment=self.environment, execution_profile=self.profile)
        self.assertEqual(WorkerCompletion(True), executor.execute(self.context))
        self.assertEqual(1, len(transport.plans))
        for result in (CodexProcessCompletion(True, False, 0), CodexProcessCompletion(True, True, 0, cleanup_confirmed=False), None):
            transport = FakeTransport(result)
            executor = CodexWorkerExecutor(transport, codex_path=r"C:\\Tools\\codex.exe", observed_codex_version=CODEX_CLI_VERSION,
                                           host_environment=self.environment, execution_profile=self.profile)
            self.assertEqual(WorkerCompletion(False), executor.execute(self.context))
            self.assertEqual(1, len(transport.plans))
        transport = FakeTransport(error=RuntimeError(self.secret))
        executor = CodexWorkerExecutor(transport, codex_path=r"C:\\Tools\\codex.exe", observed_codex_version=CODEX_CLI_VERSION,
                                       host_environment=self.environment, execution_profile=self.profile)
        outcome = executor.execute(self.context)
        self.assertEqual(WorkerCompletion(False), outcome)
        self.assertNotIn(self.secret, repr(outcome))

    def test_module_has_no_live_process_route_and_import_is_inert(self):
        source = Path(__file__).parents[1].joinpath("codex_autonomy_runner", "codex_worker_executor.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "Popen", "os.system", "shell=True", "multiprocessing", "Start-Process", "run_native_process"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
