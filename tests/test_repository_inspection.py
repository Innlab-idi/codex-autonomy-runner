from pathlib import Path
import tempfile
import unittest

from codex_autonomy_runner.native_process import run_native_process
from codex_autonomy_runner.repository_inspection import (
    RepositoryInspectionError,
    inspect_repository,
)


class RepositoryInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository = Path(self.temporary_directory.name) / "repository"
        self.repository.mkdir()
        self.git("init")
        self.write("tracked.txt", "initial\n")
        self.git("add", "tracked.txt")
        self.git("-c", "user.name=Test User", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")

    def git(self, *arguments):
        result = run_native_process(("git", *arguments), cwd=self.repository)
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def write(self, relative_path, content):
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_resolves_root_head_and_attached_branch(self):
        inspection = inspect_repository(self.repository)

        self.assertEqual(self.repository.resolve(), inspection.root)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), inspection.head_sha)
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), inspection.branch)
        self.assertFalse(inspection.is_detached)

    def test_resolves_root_from_subdirectory(self):
        nested = self.repository / "nested" / "child"
        nested.mkdir(parents=True)

        self.assertEqual(self.repository.resolve(), inspect_repository(nested).root)

    def test_detached_head_has_no_branch(self):
        self.git("checkout", "--detach")

        inspection = inspect_repository(self.repository)

        self.assertTrue(inspection.is_detached)
        self.assertIsNone(inspection.branch)

    def test_clean_repository_has_empty_changed_path_inventory(self):
        changed_paths = inspect_repository(self.repository).changed_paths

        self.assertEqual((), changed_paths.staged)
        self.assertEqual((), changed_paths.unstaged)
        self.assertEqual((), changed_paths.untracked)

    def test_tracked_worktree_change_is_unstaged_only(self):
        self.write("tracked.txt", "worktree\n")

        changed_paths = inspect_repository(self.repository).changed_paths

        self.assertEqual((), changed_paths.staged)
        self.assertEqual(("tracked.txt",), changed_paths.unstaged)

    def test_staged_change_is_reported_separately(self):
        self.write("tracked.txt", "staged\n")
        self.git("add", "tracked.txt")

        changed_paths = inspect_repository(self.repository).changed_paths

        self.assertEqual(("tracked.txt",), changed_paths.staged)
        self.assertEqual((), changed_paths.unstaged)

    def test_staged_then_worktree_change_appears_in_both_areas(self):
        self.write("tracked.txt", "staged\n")
        self.git("add", "tracked.txt")
        self.write("tracked.txt", "staged then worktree\n")

        changed_paths = inspect_repository(self.repository).changed_paths

        self.assertEqual(("tracked.txt",), changed_paths.staged)
        self.assertEqual(("tracked.txt",), changed_paths.unstaged)

    def test_deleted_tracked_path_is_reported_in_worktree(self):
        (self.repository / "tracked.txt").unlink()

        self.assertEqual(
            ("tracked.txt",), inspect_repository(self.repository).changed_paths.unstaged
        )

    def test_untracked_paths_preserve_nested_spaces_and_unicode_names(self):
        self.write("nested/file.txt", "nested\n")
        self.write("name with spaces.txt", "spaces\n")
        self.write("mañana.txt", "unicode\n")

        self.assertEqual(
            ("mañana.txt", "name with spaces.txt", "nested/file.txt"),
            inspect_repository(self.repository).changed_paths.untracked,
        )

    def test_rename_reports_both_paths_without_rename_detection(self):
        self.git("mv", "tracked.txt", "renamed.txt")

        self.assertEqual(
            ("renamed.txt", "tracked.txt"),
            inspect_repository(self.repository).changed_paths.staged,
        )

    def test_non_repository_raises_explicit_inspection_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RepositoryInspectionError):
                inspect_repository(directory)

    def test_repository_without_a_resolvable_head_raises_explicit_inspection_error(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "unborn"
            repository.mkdir()
            result = run_native_process(("git", "init"), cwd=repository)
            self.assertEqual(0, result.returncode, result.stderr)

            with self.assertRaises(RepositoryInspectionError):
                inspect_repository(repository)


if __name__ == "__main__":
    unittest.main()
