"""tmux-backed Claude Code sessions."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


class TmuxError(RuntimeError):
    """Raised when a tmux operation fails."""


def tmux_bin() -> str:
    path = shutil.which("tmux")
    if not path:
        raise TmuxError("tmux 未安装或不在 PATH 中")
    return path


def claude_bin() -> str:
    path = os.getenv("CLAUDE_CODE_BIN") or shutil.which("claude")
    if not path:
        raise TmuxError("Claude Code CLI not found. Install `claude` or set CLAUDE_CODE_BIN.")
    return path


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


def sanitize_session_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in value)
    safe = safe.strip("-") or "task"
    return f"evo-{safe[:80]}"


def start_command_session(session_id: str, cmd: list[str], log_dir: str | Path, cwd: str = "") -> dict[str, str]:
    """Start a command in an interactive tmux session and pipe pane output to a log."""
    tmux = tmux_bin()
    session = sanitize_session_name(session_id)
    target_cwd = Path(cwd or os.getcwd()).expanduser().resolve()
    if not target_cwd.is_dir():
        raise TmuxError(f"仓库路径不存在或不是目录：{target_cwd}")

    logs = Path(log_dir).expanduser().resolve()
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{session}.log"
    log_path.touch(exist_ok=True)

    if session_exists(session):
        raise TmuxError(f"tmux session 已存在：{session}")

    subprocess.run(
        [tmux, "new-session", "-d", "-s", session, "-c", str(target_cwd), shlex.join(cmd)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([tmux, "set-option", "-t", session, "history-limit", "50000"], check=False)
    subprocess.run(
        [tmux, "pipe-pane", "-t", session, "-o", f"cat >> {shlex.quote(str(log_path))}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "session": session,
        "log_path": str(log_path),
        "cwd": str(target_cwd),
        "attach_command": f"tmux attach -t {shlex.quote(session)}",
    }


def session_exists(session: str) -> bool:
    try:
        subprocess.run(
            [tmux_bin(), "has-session", "-t", session],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (TmuxError, subprocess.CalledProcessError):
        return False


def send_text(session: str, text: str):
    """Paste text into a tmux pane and press Enter."""
    if not session_exists(session):
        raise TmuxError(f"tmux session 不存在：{session}")
    tmux = tmux_bin()
    buffer_name = f"{session}-input"
    subprocess.run(
        [tmux, "load-buffer", "-b", buffer_name, "-"],
        input=text,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [tmux, "paste-buffer", "-b", buffer_name, "-t", session],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([tmux, "send-keys", "-t", session, "Enter"], check=True)


def send_keys(session: str, *keys: str):
    """Send raw tmux key names to a pane."""
    if not session_exists(session):
        raise TmuxError(f"tmux session 不存在：{session}")
    if not keys:
        return
    subprocess.run([tmux_bin(), "send-keys", "-t", session, *keys], check=True)


def stop_session(session: str):
    if not session_exists(session):
        return
    subprocess.run([tmux_bin(), "kill-session", "-t", session], check=True)


def capture_pane(session: str, history_lines: int = 200) -> str:
    """Return tmux pane text as rendered screen content."""
    if not session_exists(session):
        return ""
    start = f"-{max(history_lines, 1)}"
    try:
        result = subprocess.run(
            [tmux_bin(), "capture-pane", "-t", session, "-p", "-S", start],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (TmuxError, subprocess.CalledProcessError):
        return ""
    lines = [line.rstrip() for line in result.stdout.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def read_log_tail(path: str | Path, max_bytes: int = 40000) -> tuple[str, int]:
    if not path:
        return "", 0
    log_path = Path(path)
    if not log_path.exists() or not log_path.is_file():
        return "", 0
    size = log_path.stat().st_size
    with log_path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    return ANSI_RE.sub("", text), size
