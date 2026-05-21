# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11+ src-layout project. Core package code lives in `src/evo/`.
Key modules:

- `src/evo/main.py`: CLI entry point for `evo`.
- `src/evo/agents/`: agent implementations and Claude Code CLI integration.
- `src/evo/orchestrator/`: LangGraph workflow construction, routing, display, and validation.
- `src/evo/evolution/`: trajectory tracking, scoring, pattern storage, and prompt optimization.
- `src/evo/web/`: FastAPI dashboard and HTML template assets.
- `config/workflows.yaml`: configurable agents and workflows.
- `tests/`: unittest-based test coverage.

Avoid committing generated state such as `.venv/`, `.claude/sessions/`, `__pycache__/`, and local `.env` secrets.

## Build, Test, and Development Commands

- `make install`: create `.venv` and install the package editable.
- `make web`: start the FastAPI dashboard via `.venv/bin/evo web` on `HOST`/`PORT` (`127.0.0.1:8080` by default).
- `make list`: list configured workflows.
- `make validate`: validate workflow configuration.
- `.venv/bin/python -m unittest discover tests`: run all tests.
- `PYTHONPATH=src python -m unittest discover tests`: fallback test command when not using the virtualenv.
- `npx pyright`: run static type checking using `pyrightconfig.json`.

Prefer `.venv/bin/python` for local commands. The system Python may not have project dependencies such as `python-dotenv`, which can cause false import failures.

## Coding Style & Naming Conventions

Use 4-space indentation and standard Python naming: `snake_case` for functions, variables, and modules; `PascalCase` for classes; uppercase for constants. Keep functions focused and prefer typed signatures where practical. Follow existing patterns before adding new abstractions, especially in workflow parser, agent, and tracker code.

There is no repository formatter configured; keep imports tidy, avoid broad rewrites, and preserve the existing concise style.

## Testing Guidelines

Tests use Python `unittest` and should be named `tests/test_*.py`. Add focused tests for new behavior, especially around agent stream parsing, workflow routing, task management, and persistence. Mock external Claude Code CLI calls rather than invoking real agents in unit tests.

Run `.venv/bin/python -m unittest discover tests` before handing off changes.

## Commit & Pull Request Guidelines

Current history uses short AI-generated Chinese summaries such as `#AI commit# [100%] 修改 src/evo/task.py`. If writing manually, use concise imperative messages that name the changed area, for example `Fix empty agent stream logs`.

Pull requests should include a short description, test commands run, any workflow/config changes, and screenshots only when the web dashboard UI changes.

## Security & Configuration Tips

Configuration is loaded from `.env`, but system environment variables take precedence. Do not commit API keys, Claude configuration, local task artifacts, or generated session logs. Use `CLAUDE_CODE_BIN=/path/to/claude` when the CLI is not on `PATH`.
