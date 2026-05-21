"""Task manager - handles task lifecycle, worktrees, artifacts, and archiving."""

import json
import os
import random
import shutil
import string
import subprocess
from typing import Any, NotRequired, TypedDict


def _run_git(*args: str, cwd: str = "", check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command with UTF-8 encoding (avoids GBK codec errors on Windows)."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd or None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )

# Directories to exclude when copying non-git projects
_EXCLUDE_DIRS = frozenset({
    "node_modules", ".dist", "dist", "build", ".next", ".nuxt",
    "__pycache__", ".venv", "venv", "env", ".git", ".evo",
    ".tox", ".mypy_cache", ".pytest_cache", "target", "bin", "obj",
    ".gradle", ".idea", ".vscode", ".cache",
})


class TaskMeta(TypedDict):
    task_id: str
    task_name: str
    dir_name: str
    task_dir: str
    worktree_path: str
    repo_path: str
    repo_branch: str
    task_branch: NotRequired[str]
    description: str


class TaskManager:
    """Manages task directories, worktrees, artifacts, and archiving."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or os.path.join(os.path.expanduser("~"), ".evo")

    # ── Task creation ──────────────────────────────────────────

    def create_task(self, description: str, repo_path: str = "", branch: str = "main") -> TaskMeta:
        """Create a new task with directory, worktree, and metadata.

        Returns a dict of task fields to merge into WorkflowState.
        """
        task_id = self._generate_task_id()
        task_name = self._generate_task_name(description)
        dir_name = f"{task_id}-{task_name}"

        task_dir = os.path.join(self.base_dir, "tasks", dir_name)
        os.makedirs(task_dir, exist_ok=True)

        # Resolve repo path
        if not repo_path:
            repo_path = os.getcwd()

        if not self._is_remote_repo(repo_path):
            repo_path = os.path.abspath(repo_path)

        worktree_path = os.path.join(task_dir, "worktree")

        # Setup worktree or copy code
        is_git = self._is_remote_repo(repo_path) or self._is_git_repo(repo_path)
        resolved_branch = branch
        if is_git:
            resolved_branch, task_branch, repo_path = self._setup_worktree(worktree_path, repo_path, branch, dir_name)
        else:
            task_branch = ""
            self._copy_code(worktree_path, repo_path)

        # Save task metadata
        meta: TaskMeta = {
            "task_id": task_id,
            "task_name": task_name,
            "dir_name": dir_name,
            "task_dir": task_dir,
            "worktree_path": worktree_path,
            "repo_path": repo_path,
            "repo_branch": resolved_branch,
            "task_branch": task_branch,
            "description": description,
        }
        self._save_meta(task_dir, meta)

        return meta

    # ── Artifact writing ───────────────────────────────────────

    def write_artifact(self, task_dir: str, name: str, content: str):
        """Write an artifact file to the task directory."""
        if not task_dir or not content:
            return
        os.makedirs(task_dir, exist_ok=True)
        path = os.path.join(task_dir, f"{name}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def write_state(self, task_dir: str, state: dict[str, Any]):
        """Write the full workflow state as JSON."""
        if not task_dir:
            return
        os.makedirs(task_dir, exist_ok=True)
        # Strip leading-underscore internal keys
        clean = {k: v for k, v in state.items() if not k.startswith("_")}
        # Truncate very long values for readability
        for k, v in clean.items():
            if isinstance(v, str) and len(v) > 5000:
                clean[k] = v[:5000] + "\n... (truncated)"
        path = os.path.join(task_dir, "state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)

    # ── Archiving ──────────────────────────────────────────────

    def archive_task(self, task_id_or_dir: str) -> str:
        """Archive a task: merge branch, remove worktree, delete branch, move to archive/."""
        task_dir = self._find_task_dir(task_id_or_dir)
        meta = self._load_meta(task_dir)
        if meta is None:
            raise FileNotFoundError(f"No task metadata found in: {task_dir}")

        worktree_path = meta["worktree_path"]
        repo_path = meta["repo_path"]
        task_branch = meta.get("task_branch") or self._legacy_task_branch(repo_path, meta["dir_name"])
        target_branch = meta["repo_branch"]

        # Agent edits happen in the task worktree. Commit them to the task branch
        # before merging so archive includes the actual completed work.
        if os.path.isdir(worktree_path):
            _run_git("add", "-A", cwd=worktree_path)
            status = _run_git("status", "--porcelain", cwd=worktree_path)
            if status.stdout.strip():
                _run_git(
                    "-c", "user.name=Evo",
                    "-c", "user.email=evo@example.com",
                    "commit", "-m", f"Complete task {meta['task_id']}",
                    cwd=worktree_path,
                )

        # 1. Merge worktree branch into target branch
        _run_git("checkout", target_branch, cwd=repo_path)
        _run_git("merge", task_branch, "--no-edit", cwd=repo_path)

        # 2. Remove worktree
        if os.path.isdir(worktree_path):
            _run_git("worktree", "remove", worktree_path, "--force", cwd=repo_path)

        # 3. Delete the task branch
        _run_git("branch", "-d", task_branch, cwd=repo_path)

        # 4. Move task directory to archive
        archive_dir = os.path.join(self.base_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)
        archived_task_dir = os.path.join(archive_dir, os.path.basename(task_dir))
        shutil.move(task_dir, archived_task_dir)
        return archived_task_dir

    # ── Listing ────────────────────────────────────────────────

    def list_tasks(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """List all tasks (active, and optionally archived)."""
        tasks = []
        tasks_dir = os.path.join(self.base_dir, "tasks")
        if os.path.isdir(tasks_dir):
            for name in os.listdir(tasks_dir):
                d = os.path.join(tasks_dir, name)
                if os.path.isdir(d):
                    meta = self._load_meta(d)
                    if meta:
                        item: dict[str, Any] = dict(meta)
                        item["_archived"] = False
                        tasks.append(item)

        if include_archived:
            archive_dir = os.path.join(self.base_dir, "archive")
            if os.path.isdir(archive_dir):
                for name in os.listdir(archive_dir):
                    d = os.path.join(archive_dir, name)
                    if os.path.isdir(d):
                        meta = self._load_meta(d)
                        if meta:
                            item = dict(meta)
                            item["_archived"] = True
                            tasks.append(item)

        return tasks

    # ── ID / Name generation ───────────────────────────────────

    def _generate_task_id(self) -> str:
        chars = string.ascii_lowercase + string.digits
        return "".join(random.choices(chars, k=6))

    def _generate_task_name(self, description: str) -> str:
        """Generate a short task name without direct LLM API calls."""
        return self._sanitize_name(description)

    @staticmethod
    def _sanitize_name(description: str) -> str:
        """Fallback: produce a filesystem-safe name from the description."""
        # Take first meaningful chars, replace spaces
        name = description.strip()[:8].replace(" ", "_")
        # Remove chars unsafe for directory names
        safe = ""
        for ch in name:
            if ch.isalnum() or ch in "_-":
                safe += ch
        return safe or "task"

    # ── Worktree / Copy ────────────────────────────────────────

    def _setup_worktree(self, worktree_path: str, repo_path: str, branch: str, dir_name: str) -> tuple[str, str, str]:
        """Create a git worktree from the repo. Returns source branch, task branch, and repo path."""
        task_branch = self._task_branch_name(dir_name)

        # If repo_path is a remote URL, clone to cache first
        if self._is_remote_repo(repo_path):
            repo_path = self._cache_repo(repo_path)

        # Resolve the actual default branch if user specified a non-existent one
        resolved_branch = self._resolve_branch(repo_path, branch)

        result = _run_git("worktree", "add", worktree_path, "-b", task_branch, resolved_branch,
                          cwd=repo_path, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create worktree: {result.stderr}")

        return resolved_branch, task_branch, repo_path

    @staticmethod
    def _is_remote_repo(repo_path: str) -> bool:
        """Return whether repo_path is a supported remote Git URL."""
        return repo_path.startswith(("http://", "https://", "git@"))

    @staticmethod
    def _is_git_repo(repo_path: str) -> bool:
        """Return whether repo_path points to a local Git working tree."""
        if not os.path.isdir(repo_path):
            return False

        result = _run_git("rev-parse", "--is-inside-work-tree", cwd=repo_path, check=False)
        return result.returncode == 0 and result.stdout.strip() == "true"

    @staticmethod
    def _task_branch_name(dir_name: str) -> str:
        """Return the branch name used for generated task worktrees."""
        return f"evo-task/{dir_name}"

    @staticmethod
    def _legacy_task_branch(repo_path: str, dir_name: str) -> str:
        """Find the task branch for metadata created before task_branch existed."""
        candidates = [f"evo-task/{dir_name}", f"evo/{dir_name}"]
        for candidate in candidates:
            result = _run_git("rev-parse", "--verify", candidate, cwd=repo_path, check=False)
            if result.returncode == 0:
                return candidate
        return candidates[0]

    @staticmethod
    def _resolve_branch(repo_path: str, branch: str) -> str:
        """Resolve branch name — if the specified branch doesn't exist, find the default."""
        result = _run_git("rev-parse", "--verify", branch, cwd=repo_path, check=False)
        if result.returncode == 0:
            return branch
        # Try common default branch names
        for candidate in ["main", "master", "trunk", "develop"]:
            result = _run_git("rev-parse", "--verify", candidate, cwd=repo_path, check=False)
            if result.returncode == 0:
                return candidate
        # Last resort: use HEAD
        return "HEAD"

    def _copy_code(self, worktree_path: str, source_path: str):
        """Copy source code (excluding large/irrelevant dirs) for non-git projects."""
        def _ignore(directory: str, files: list[str]) -> list[str]:
            return [f for f in files if f in _EXCLUDE_DIRS]

        shutil.copytree(source_path, worktree_path, ignore=_ignore)

    def _cache_repo(self, repo_url: str) -> str:
        """Clone a remote repo to .evo/repos/ as a bare mirror cache. Returns local path."""
        import hashlib

        repo_hash = hashlib.md5(repo_url.encode()).hexdigest()[:12]
        cache_path = os.path.join(self.base_dir, "repos", repo_hash)

        if os.path.isdir(cache_path):
            # Update existing cache
            _run_git("fetch", "--all", cwd=cache_path, check=False)
            return cache_path

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        result = _run_git("clone", "--bare", repo_url, cache_path, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone repo: {result.stderr}")
        return cache_path

    # ── Metadata helpers ───────────────────────────────────────

    def _save_meta(self, task_dir: str, meta: TaskMeta):
        path = os.path.join(task_dir, "task_meta.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _load_meta(self, task_dir: str) -> TaskMeta | None:
        path = os.path.join(task_dir, "task_meta.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        required = {
            "task_id", "task_name", "dir_name", "task_dir",
            "worktree_path", "repo_path", "repo_branch", "description",
        }
        if not required.issubset(data):
            return None
        return TaskMeta(
            task_id=str(data["task_id"]),
            task_name=str(data["task_name"]),
            dir_name=str(data["dir_name"]),
            task_dir=str(data["task_dir"]),
            worktree_path=str(data["worktree_path"]),
            repo_path=str(data["repo_path"]),
            repo_branch=str(data["repo_branch"]),
            task_branch=str(data["task_branch"]) if data.get("task_branch") else "",
            description=str(data["description"]),
        )

    def _find_task_dir(self, task_id_or_dir: str) -> str:
        """Find a task directory by ID prefix or full dir name."""
        # If it's already a full path
        if os.path.isdir(task_id_or_dir):
            return task_id_or_dir

        # Search in tasks/
        tasks_dir = os.path.join(self.base_dir, "tasks")
        if os.path.isdir(tasks_dir):
            for name in os.listdir(tasks_dir):
                if name.startswith(task_id_or_dir) or task_id_or_dir in name:
                    candidate = os.path.join(tasks_dir, name)
                    if os.path.isdir(candidate):
                        return candidate

        raise FileNotFoundError(f"Task not found: {task_id_or_dir}")
