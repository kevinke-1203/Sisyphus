import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evo.task import TaskManager


def _git(*args: str, cwd: str):
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TaskManagerWorktreeTests(unittest.TestCase):
    def test_create_task_preserves_remote_url_until_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = TaskManager(base_dir=tmp)
            cached_repo = os.path.join(tmp, "repos", "cached")

            with patch.object(
                manager,
                "_setup_worktree",
                return_value=("main", "evo-task/example", cached_repo),
            ) as setup_worktree:
                meta = manager.create_task(
                    "remote task",
                    repo_path="https://example.com/acme/project.git",
                    branch="main",
                )

            self.assertEqual(meta["repo_path"], cached_repo)
            self.assertEqual(meta["repo_branch"], "main")
            setup_worktree.assert_called_once()
            self.assertEqual(setup_worktree.call_args.args[1], "https://example.com/acme/project.git")

    def test_create_task_accepts_git_worktree_with_git_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            linked_worktree = os.path.join(tmp, "linked")
            task_base = os.path.join(tmp, "evo")

            os.mkdir(repo)
            _git("init", "-b", "main", cwd=repo)
            _git("config", "user.email", "evo@example.com", cwd=repo)
            _git("config", "user.name", "Evo Test", cwd=repo)

            with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
                f.write("# example\n")
            _git("add", "README.md", cwd=repo)
            _git("commit", "-m", "initial", cwd=repo)

            _git("worktree", "add", linked_worktree, "-b", "linked-branch", "main", cwd=repo)

            self.assertTrue(os.path.isfile(os.path.join(linked_worktree, ".git")))

            manager = TaskManager(base_dir=task_base)
            meta = manager.create_task("worktree task", repo_path=linked_worktree, branch="main")

            self.assertEqual(meta["repo_path"], linked_worktree)
            self.assertTrue(meta["task_branch"].startswith("evo-task/"))
            self.assertTrue(os.path.isdir(meta["worktree_path"]))
            self.assertTrue(os.path.exists(os.path.join(meta["worktree_path"], ".git")))

    def test_archive_task_commits_and_merges_worktree_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            task_base = os.path.join(tmp, "evo")

            os.mkdir(repo)
            _git("init", "-b", "main", cwd=repo)
            _git("config", "user.email", "evo@example.com", cwd=repo)
            _git("config", "user.name", "Evo Test", cwd=repo)

            with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as f:
                f.write("# example\n")
            _git("add", "README.md", cwd=repo)
            _git("commit", "-m", "initial", cwd=repo)

            manager = TaskManager(base_dir=task_base)
            meta = manager.create_task("merge task", repo_path=repo, branch="main")

            changed_file = os.path.join(meta["worktree_path"], "feature.txt")
            with open(changed_file, "w", encoding="utf-8") as f:
                f.write("implemented\n")

            archived_task_dir = manager.archive_task(meta["task_id"])

            self.assertFalse(os.path.exists(meta["task_dir"]))
            self.assertTrue(os.path.isdir(archived_task_dir))
            self.assertFalse(os.path.isdir(meta["worktree_path"]))
            with open(os.path.join(repo, "feature.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "implemented\n")
            self.assertEqual(_git("branch", "--show-current", cwd=repo).stdout.strip(), "main")


if __name__ == "__main__":
    unittest.main()
