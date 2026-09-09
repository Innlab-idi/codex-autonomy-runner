from dataclasses import FrozenInstanceError
import unittest

from codex_autonomy_runner.changed_path_validation import validate_changed_paths
from codex_autonomy_runner.repository_inspection import ChangedPaths


def changed_paths(staged=(), unstaged=(), untracked=()):
    return ChangedPaths(staged=staged, unstaged=unstaged, untracked=untracked)


class ChangedPathValidationTests(unittest.TestCase):
    def test_empty_inventory_and_allowlist_is_valid(self):
        result = validate_changed_paths(changed_paths(), ())

        self.assertEqual((), result.actual_paths)
        self.assertEqual((), result.permitted_paths)
        self.assertEqual((), result.unexpected_paths)
        self.assertTrue(result.is_valid)

    def test_each_change_area_contributes_authorized_paths(self):
        result = validate_changed_paths(
            changed_paths(staged=("staged.py",), unstaged=("worktree.py",), untracked=("new.py",)),
            ("new.py", "staged.py", "worktree.py"),
        )

        self.assertEqual(("new.py", "staged.py", "worktree.py"), result.actual_paths)
        self.assertEqual(result.actual_paths, result.permitted_paths)
        self.assertEqual((), result.unexpected_paths)
        self.assertTrue(result.is_valid)

    def test_unexpected_path_is_preserved_and_invalidates_result(self):
        result = validate_changed_paths(changed_paths(untracked=("surprise.txt",)), ())

        self.assertEqual(("surprise.txt",), result.actual_paths)
        self.assertEqual(("surprise.txt",), result.unexpected_paths)
        self.assertFalse(result.is_valid)

    def test_duplicate_path_across_all_change_areas_appears_once(self):
        result = validate_changed_paths(
            changed_paths(staged=("same.py",), unstaged=("same.py",), untracked=("same.py",)),
            ("same.py",),
        )

        self.assertEqual(("same.py",), result.actual_paths)
        self.assertEqual(("same.py",), result.permitted_paths)

    def test_permitted_and_unexpected_paths_partition_actual_paths(self):
        result = validate_changed_paths(
            changed_paths(staged=("allowed.py", "unexpected.py")), ("allowed.py", "unchanged.py")
        )

        self.assertEqual(("allowed.py", "unexpected.py"), result.actual_paths)
        self.assertEqual(("allowed.py",), result.permitted_paths)
        self.assertEqual(("unexpected.py",), result.unexpected_paths)
        self.assertNotIn("unchanged.py", result.actual_paths)

    def test_allowlist_duplicates_and_input_order_do_not_change_result(self):
        inventory = changed_paths(staged=("b.py", "a.py"), untracked=("c.py",))

        first = validate_changed_paths(inventory, ("c.py", "a.py", "a.py"))
        second = validate_changed_paths(
            changed_paths(staged=("a.py", "b.py"), untracked=("c.py",)), ("a.py", "c.py")
        )

        self.assertEqual(first, second)
        self.assertEqual(("a.py", "b.py", "c.py"), first.actual_paths)

    def test_matching_is_case_sensitive_without_prefix_or_directory_inference(self):
        result = validate_changed_paths(
            changed_paths(untracked=("A/file.py", "dir/file.py", "module.py")),
            ("a/file.py", "dir", "*.py"),
        )

        self.assertEqual((), result.permitted_paths)
        self.assertEqual(("A/file.py", "dir/file.py", "module.py"), result.unexpected_paths)

    def test_paths_are_not_normalized(self):
        result = validate_changed_paths(changed_paths(unstaged=("a/../b.py",)), ("b.py",))

        self.assertEqual(("a/../b.py",), result.unexpected_paths)
        self.assertFalse(result.is_valid)

    def test_spaces_and_unicode_are_preserved_exactly(self):
        path = "dir/mañana file.py"
        result = validate_changed_paths(changed_paths(untracked=(path,)), (path,))

        self.assertEqual((path,), result.actual_paths)
        self.assertEqual((path,), result.permitted_paths)

    def test_result_and_path_collections_are_immutable(self):
        result = validate_changed_paths(changed_paths(staged=("a.py",)), ("a.py",))

        self.assertIsInstance(result.actual_paths, tuple)
        self.assertIsInstance(result.permitted_paths, tuple)
        self.assertIsInstance(result.unexpected_paths, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.actual_paths = ()


if __name__ == "__main__":
    unittest.main()
