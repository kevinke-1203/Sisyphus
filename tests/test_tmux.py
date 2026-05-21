import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evo import tmux


class TmuxTests(unittest.TestCase):
    def test_sanitize_session_name(self):
        self.assertEqual(tmux.sanitize_session_name("abc/123"), "evo-abc-123")
        self.assertEqual(tmux.sanitize_session_name(""), "evo-task")

    def test_read_log_tail_strips_ansi(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tmux.log"
            path.write_bytes(b"\x1b[31mred\x1b[0m\nplain")

            text, size = tmux.read_log_tail(path)

        self.assertEqual(text, "red\nplain")
        self.assertGreater(size, len(text))

    def test_read_log_tail_ignores_empty_path_and_directories(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tmux.read_log_tail(""), ("", 0))
            self.assertEqual(tmux.read_log_tail(d), ("", 0))

    def test_capture_pane_trims_blank_edges(self):
        def fake_run(cmd, **kwargs):
            class Result:
                stdout = "\n\nfirst\nsecond   \n\n"

            return Result()

        with patch("evo.tmux.session_exists", return_value=True), patch("subprocess.run", fake_run):
            self.assertEqual(tmux.capture_pane("evo-abc"), "first\nsecond")

    def test_capture_pane_failure_returns_empty_screen(self):
        def fake_run(cmd, **kwargs):
            raise tmux.subprocess.CalledProcessError(1, cmd)

        with patch("evo.tmux.session_exists", return_value=True), patch("subprocess.run", fake_run):
            self.assertEqual(tmux.capture_pane("evo-abc"), "")

    def test_start_command_session_returns_tmux_metadata(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with tempfile.TemporaryDirectory() as d, patch("shutil.which") as which, patch(
            "subprocess.run", fake_run
        ), patch("evo.tmux.session_exists", return_value=False):
            which.side_effect = lambda name: f"/bin/{name}"
            result = tmux.start_command_session("abc123", ["/bin/claude", "--flag"], d, cwd=d)

        self.assertEqual(result["session"], "evo-abc123")
        self.assertIn("tmux attach -t evo-abc123", result["attach_command"])
        self.assertTrue(result["log_path"].endswith("evo-abc123.log"))
        self.assertIn(
            ["/bin/tmux", "new-session", "-d", "-s", "evo-abc123", "-c", str(Path(d).resolve()), "/bin/claude --flag"],
            calls,
        )

    def test_send_keys_sends_raw_tmux_keys(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch("shutil.which", return_value="/bin/tmux"), patch("subprocess.run", fake_run), patch(
            "evo.tmux.session_exists", return_value=True
        ):
            tmux.send_keys("evo-abc123", "Enter")

        self.assertEqual(calls, [["/bin/tmux", "send-keys", "-t", "evo-abc123", "Enter"]])


if __name__ == "__main__":
    unittest.main()
