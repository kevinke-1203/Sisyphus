"""Structured logging and output formatting for Evo."""

import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()

_step_costs: list[dict] = []


def header(task: str, workflow: str, max_iter: int):
    console.print()
    console.rule("[bold blue]Evo Workflow", style="blue")
    console.print(f"  Task:       {task}")
    console.print(f"  Workflow:   {workflow}")
    console.print(f"  Max iter:   {max_iter}")
    console.rule(style="blue")
    console.print()


def step_start(node_name: str, iteration: int | None = None):
    label = f"[bold cyan]{node_name}[/]"
    if iteration and iteration > 1:
        label += f" [dim](iteration {iteration})[/]"
    console.print(f"  -> {label}", end="")


def step_end(duration_ms: int, success: bool, detail: str = ""):
    elapsed = f"{duration_ms/1000:.1f}s"
    if success:
        status = f"[green]{elapsed}[/]"
    else:
        status = f"[red]{elapsed} FAILED[/]"
    line = f" {status}"
    if detail:
        line += f" [dim]({detail})[/]"
    console.print(line)


def footer(passed: bool, iterations: int, trajectory_id: str, total_ms: int, total_tokens: int):
    console.print()
    console.rule(style="blue")

    status = "[bold green]PASSED[/]" if passed else "[bold red]FAILED[/]"
    console.print(f"  Result:      {status}")
    console.print(f"  Iterations:  {iterations}")
    console.print(f"  Trajectory:  {trajectory_id}")
    console.print(f"  Duration:    {total_ms/1000:.1f}s")
    if total_tokens > 0:
        console.print(f"  Tokens:      ~{total_tokens}")
    console.rule(style="blue")
    console.print()


def print_table(title: str, columns: list[tuple[str, str]], rows: list[dict]):
    """Print a formatted table."""
    table = Table(title=title, show_lines=False)
    for col_name, col_style in columns:
        table.add_column(col_name, style=col_style)
    for row in rows:
        table.add_row(*[str(row.get(col[0].lower().replace(" ", "_"), "")) for col in columns])
    console.print(table)


def error(msg: str):
    console.print(f"[bold red]Error:[/] {msg}")


def info(msg: str):
    console.print(f"[dim]{msg}[/]")


def code_output(code: str):
    """Print code in a panel."""
    if len(code) > 2000:
        code = code[:2000] + "\n... (truncated)"
    console.print(Panel(code, title="Generated Code", border_style="green", expand=False))
