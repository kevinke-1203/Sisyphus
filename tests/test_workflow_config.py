import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evo.agents.registry import get_agent
from evo.orchestrator.parser import load_config, register_agents_from_config


class WorkflowConfigTests(unittest.TestCase):
    def test_register_agents_applies_skills_to_builtin_agent(self):
        register_agents_from_config({"agents": {"coder": {"skills": ["browser:browser", "/imagegen"]}}})

        agent = get_agent("coder")

        self.assertIsNotNone(agent)
        self.assertEqual(agent.config.skills, ["browser:browser", "/imagegen"])

    def test_load_config_falls_back_to_packaged_default(self):
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as home:
            with patch.object(Path, "home", return_value=Path(home)):
                with patch.dict(os.environ, {"EVO_WORKFLOW_CONFIG": ""}):
                    old_cwd = os.getcwd()
                    try:
                        os.chdir(cwd)
                        config = load_config()
                    finally:
                        os.chdir(old_cwd)

        self.assertIn("agents", config)
        self.assertIn("plan_code_test", config.get("workflows", {}))

    def test_load_config_prefers_local_config(self):
        with tempfile.TemporaryDirectory() as cwd:
            config_dir = Path(cwd) / "config"
            config_dir.mkdir()
            (config_dir / "workflows.yaml").write_text(
                "workflows:\n  local:\n    description: local override\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"EVO_WORKFLOW_CONFIG": ""}):
                old_cwd = os.getcwd()
                try:
                    os.chdir(cwd)
                    config = load_config()
                finally:
                    os.chdir(old_cwd)

        self.assertIn("local", config.get("workflows", {}))
        self.assertNotIn("plan_code_test", config.get("workflows", {}))

    def test_load_config_accepts_explicit_file(self):
        with tempfile.TemporaryDirectory() as cwd:
            config_path = Path(cwd) / "external-workflow.yaml"
            config_path.write_text(
                "workflows:\n  external:\n    description: external file\n",
                encoding="utf-8",
            )

            config = load_config(str(config_path))

        self.assertIn("external", config.get("workflows", {}))

    def test_load_config_accepts_explicit_directory_with_workflow_yaml(self):
        with tempfile.TemporaryDirectory() as cwd:
            config_path = Path(cwd) / "workflow.yaml"
            config_path.write_text(
                "workflows:\n  singular:\n    description: singular file\n",
                encoding="utf-8",
            )

            config = load_config(cwd)

        self.assertIn("singular", config.get("workflows", {}))

    def test_load_config_uses_env_var(self):
        with tempfile.TemporaryDirectory() as cwd:
            config_path = Path(cwd) / "workflow.yaml"
            config_path.write_text(
                "workflows:\n  env_config:\n    description: env file\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"EVO_WORKFLOW_CONFIG": str(config_path)}):
                config = load_config()

        self.assertIn("env_config", config.get("workflows", {}))


if __name__ == "__main__":
    unittest.main()
