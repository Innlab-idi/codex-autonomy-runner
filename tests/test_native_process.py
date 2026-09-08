import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from codex_autonomy_runner.native_process import (
    NativeProcessLaunchError,
    run_native_process,
)


class NativeProcessTests(unittest.TestCase):
    def run_child(self, program, *arguments, **kwargs):
        return run_native_process((sys.executable, "-c", program, *arguments), **kwargs)

    def test_successful_process_returns_zero_exit_code(self):
        result = self.run_child("import sys; sys.stdout.write('ok')")

        self.assertEqual(0, result.returncode)
        self.assertEqual("ok", result.stdout)
        self.assertEqual("", result.stderr)

    def test_non_zero_exit_is_a_process_result_not_a_launch_failure(self):
        result = self.run_child(
            "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(7)"
        )

        self.assertEqual(7, result.returncode)
        self.assertEqual("out", result.stdout)
        self.assertEqual("err", result.stderr)

    def test_stdout_and_stderr_are_captured_independently(self):
        result = self.run_child("import sys; sys.stdout.write('stdout'); sys.stderr.write('stderr')")

        self.assertEqual("stdout", result.stdout)
        self.assertEqual("stderr", result.stderr)

    def test_argv_preserves_spaces_and_quotes_in_one_argument(self):
        argument = 'a value with spaces and "quoted text"'
        result = self.run_child(
            "import json, sys; sys.stdout.write(json.dumps(sys.argv[1]))", argument
        )

        self.assertEqual(0, result.returncode)
        self.assertEqual(argument, json.loads(result.stdout))

    def test_utf8_output_is_decoded_explicitly(self):
        result = self.run_child(
            "import sys; sys.stdout.buffer.write('\\u00f1'.encode('utf-8'))"
        )

        self.assertEqual(0, result.returncode)
        self.assertEqual("ñ", result.stdout)

    def test_utf8_argument_reaches_the_child_process(self):
        argument = "mañana"
        result = self.run_child(
            "import json, sys; sys.stdout.buffer.write(json.dumps(sys.argv[1], ensure_ascii=False).encode('utf-8'))",
            argument,
        )

        self.assertEqual(0, result.returncode)
        self.assertEqual(argument, json.loads(result.stdout))

    def test_explicit_cwd_is_used_by_the_child_process(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = self.run_child(
                "import os, sys; sys.stdout.write(os.getcwd())", cwd=temporary_directory
            )

            self.assertEqual(0, result.returncode)
            self.assertEqual(
                os.path.normcase(os.path.abspath(temporary_directory)),
                os.path.normcase(os.path.abspath(result.stdout)),
            )

    def test_launch_failure_is_distinct_from_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_executable = Path(temporary_directory) / "definitely-not-an-executable"

            with self.assertRaises(NativeProcessLaunchError) as raised:
                run_native_process((str(missing_executable),))

        self.assertEqual((str(missing_executable),), raised.exception.argv)
        self.assertIsInstance(raised.exception.cause, OSError)


if __name__ == "__main__":
    unittest.main()
