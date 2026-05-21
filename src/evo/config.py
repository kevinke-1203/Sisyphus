"""Project isolation - separate data directories per project."""

import os

from dotenv import load_dotenv


_current_project: str = "default"


def _default_base_dir() -> str:
    """Default base directory: ~/.evo"""
    return os.path.join(os.path.expanduser("~"), ".evo")


def load_project_env():
    """Load environment variables from the current project's .env file."""
    load_dotenv(os.path.join(os.getcwd(), ".env"), override=False)


def set_project(name: str):
    """Set the active project. All data will be stored under this project's directory."""
    global _current_project
    _current_project = name
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)


def get_project() -> str:
    """Get the current active project name."""
    return _current_project


def get_data_dir() -> str:
    """Get the data directory for the current project."""
    base = os.getenv("EVO_DATA_DIR", _default_base_dir())
    if _current_project == "default":
        return base
    return os.path.join(base, "projects", _current_project)


def list_projects() -> list[str]:
    """List all projects that have data."""
    base = os.getenv("EVO_DATA_DIR", _default_base_dir())
    projects_dir = os.path.join(base, "projects")
    projects = ["default"]
    if os.path.exists(projects_dir):
        projects += [d for d in os.listdir(projects_dir) if os.path.isdir(os.path.join(projects_dir, d))]
    return projects
