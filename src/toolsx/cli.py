from __future__ import annotations

import importlib
import sys
from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from toolsx.registry import TOOLS, find_tool


console = Console()


def print_tool_list() -> None:
    title = Text("toolsx", style="bold bright_white")
    subtitle = Text("CLI toolbox", style="cyan")
    console.print(
        Panel.fit(Text.assemble(title, "\n", subtitle), border_style="bright_blue")
    )

    table = Table(title="Available Tools", header_style="bold bright_cyan")
    table.add_column("Tool", style="bold white")
    table.add_column("Command", style="green")
    table.add_column("What it does", style="dim")
    for tool in TOOLS:
        table.add_row(tool.key, tool.command, tool.summary)
    console.print(table)
    console.print(
        "[dim]Run `toolsx <tool>` to dispatch, or call the command directly.[/]"
    )


def dispatch(tool_name: str, argv: Sequence[str]) -> int:
    tool = find_tool(tool_name)
    if tool is None:
        console.print(f"[red]Unknown tool:[/] {tool_name}")
        print_tool_list()
        return 1

    module = importlib.import_module(tool.module)
    entry = getattr(module, tool.callable_name)
    original_argv = sys.argv[:]
    try:
        sys.argv = [tool.command, *argv]
        return int(entry(list(argv)))
    finally:
        sys.argv = original_argv


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print_tool_list()
        return 0
    return dispatch(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
