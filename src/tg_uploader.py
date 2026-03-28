#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Union

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text


console = Console()
ENV_KEYS = {
    "api_id": ("TOOLSX_TG_API_ID", "TG_API_ID", "TELEGRAM_API_ID"),
    "api_hash": ("TOOLSX_TG_API_HASH", "TG_API_HASH", "TELEGRAM_API_HASH"),
    "bot_token": ("TOOLSX_TG_BOT_TOKEN", "TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    "chat_id": ("TOOLSX_TG_CHAT_ID", "TG_CHAT_ID", "TELEGRAM_CHAT_ID"),
}


@dataclass
class UploadConfig:
    api_id: int
    api_hash: str
    bot_token: str
    chat_id: Union[int, str]
    file_path: Path
    caption: Optional[str]


def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        console.print(f"[dim][debug][/dim] {message}")


def format_bytes(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def format_file_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a file to Telegram using a bot session.")
    parser.add_argument("--api-id", help="Telegram API ID")
    parser.add_argument("--api-hash", help="Telegram API hash")
    parser.add_argument("--bot-token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="Target chat ID or username")
    parser.add_argument("--file", dest="file_path", help="Path to the file to upload")
    parser.add_argument("--caption", help="Optional document caption")
    parser.add_argument("--disable-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--debug", action="store_true", help="Print verbose debug logs for every step")
    return parser.parse_args(argv)


def env_value(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def prompt_value(label: str, current: Optional[str], env_names: Sequence[str], secret: bool = False) -> str:
    if current and current.strip():
        return current.strip()
    from_env = env_value(*env_names)
    if from_env:
        return from_env
    return Prompt.ask(label, password=secret).strip()


def list_pickable_files(directory: Path) -> list[Path]:
    return sorted(
        [item for item in directory.iterdir() if item.is_file() and not item.name.startswith(".")],
        key=lambda item: (item.suffix.lower(), item.name.lower()),
    )


def prompt_for_file_path(current: Optional[str]) -> str:
    if current and current.strip():
        return current.strip()

    files = list_pickable_files(Path.cwd())
    if not files:
        raise FileNotFoundError("No files found in the current directory.")

    table = Table(title="Choose a File", header_style="bold bright_cyan")
    table.add_column("#", justify="right", style="bold yellow")
    table.add_column("Name", style="bold white")
    table.add_column("Type", style="green")
    table.add_column("Size", justify="right", style="cyan")
    table.add_column("Modified", style="dim")

    for index, file_path in enumerate(files, start=1):
        stat = file_path.stat()
        table.add_row(
            str(index),
            file_path.name,
            file_path.suffix.lstrip(".") or "file",
            format_bytes(stat.st_size),
            format_file_date(stat.st_mtime),
        )

    console.print(table)
    console.print("[dim]Pick a number or paste a custom file path.[/]")

    while True:
        choice = Prompt.ask("File path / number").strip()
        if not choice:
            console.print("[yellow]Please enter a file number or file path.[/]")
            continue
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(files):
                return str(files[index - 1])
            console.print("[yellow]Invalid file number.[/]")
            continue
        return choice


def parse_chat_id(value: str) -> Union[int, str]:
    raw = value.strip()
    if not raw:
        raise ValueError("Chat ID is required.")
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


def collect_config(args: argparse.Namespace) -> UploadConfig:
    api_id_raw = prompt_value("API ID", args.api_id, ENV_KEYS["api_id"])
    api_hash = prompt_value("API hash", args.api_hash, ENV_KEYS["api_hash"], secret=True)
    bot_token = prompt_value("Bot token", args.bot_token, ENV_KEYS["bot_token"], secret=True)
    chat_id_raw = prompt_value("Chat ID", args.chat_id, ENV_KEYS["chat_id"])
    file_path_raw = prompt_for_file_path(args.file_path)
    debug_log(args.debug, f"Collected config inputs: api_id={'set' if api_id_raw else 'missing'} api_hash={'set' if api_hash else 'missing'} bot_token={'set' if bot_token else 'missing'} chat_id={chat_id_raw!r} file={file_path_raw!r}")

    if not api_id_raw.isdigit():
        raise ValueError("API ID must be numeric.")

    file_path = Path(file_path_raw).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    return UploadConfig(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        bot_token=bot_token,
        chat_id=parse_chat_id(chat_id_raw),
        file_path=file_path,
        caption=args.caption,
    )


def print_banner() -> None:
    title = Text("Telegram Uploader", style="bold bright_white")
    subtitle = Text("Rich bot upload flow with CLI args, env vars, and prompts", style="cyan")
    console.print(Panel.fit(Text.assemble(title, "\n", subtitle), border_style="bright_blue"))


def print_upload_plan(config: UploadConfig) -> None:
    file_size = config.file_path.stat().st_size
    console.print(
        Panel(
            f"[cyan]Chat:[/] {config.chat_id}\n"
            f"[green]File:[/] {config.file_path.name}\n"
            f"[cyan]Size:[/] {format_bytes(file_size)}\n"
            f"[green]Caption:[/] {config.caption or '-'}",
            title="Upload Plan",
            border_style="green",
        )
    )


async def upload_file(config: UploadConfig, debug: bool) -> None:
    Client = importlib.import_module("pyrogram").Client
    file_size = config.file_path.stat().st_size

    print_upload_plan(config)
    console.print("[dim]Connecting to Telegram...[/]")
    debug_log(debug, f"Preparing Telegram client for chat={config.chat_id!r} file={str(config.file_path)!r} size={file_size}")

    app = Client(
        name="toolsx_tg_uploader",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        in_memory=True,
        no_updates=True,
    )

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=36),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Uploading file", total=max(file_size, 1))

        def update_progress(current: int, total: int) -> None:
            progress.update(task_id, total=max(total, 1), completed=current)
            if debug:
                console.print(f"[dim]upload progress: {current}/{max(total, 1)} bytes[/]")

        async with app:
            debug_log(debug, "Opening Telegram session")
            me = await app.get_me()
            username = f"@{me.username}" if me.username else "none"
            debug_log(debug, f"Authenticated as bot id={me.id} username={username}")
            console.print(
                Panel(
                    f"[cyan]Name:[/] {me.first_name}\n"
                    f"[green]Username:[/] {username}\n"
                    f"[cyan]Bot ID:[/] {me.id}",
                    title="Bot Session",
                    border_style="magenta",
                )
            )
            debug_log(debug, "Sending document to Telegram")
            message = await app.send_document(
                chat_id=config.chat_id,
                document=str(config.file_path),
                caption=config.caption,
                force_document=True,
                progress=update_progress,
            )
            debug_log(debug, f"Upload finished with message id={message.id if message else 'none'}")

    if message is None:
        raise RuntimeError("Upload stopped before completion.")

    console.print(
        Panel(
            f"[green]Message ID:[/] {message.id}\n[cyan]Chat ID:[/] {message.chat.id}",
            title="Upload Complete",
            border_style="bright_green",
        )
    )


async def async_main(argv: Optional[Sequence[str]] = None) -> int:
    global console
    args = parse_args(argv)
    console = Console(no_color=args.disable_color)
    print_banner()
    debug_log(args.debug, f"Arguments parsed: file={args.file_path!r} chat_id={args.chat_id!r} disable_color={args.disable_color}")

    try:
        config = collect_config(args)
        await upload_file(config, args.debug)
        return 0
    except ModuleNotFoundError as error:
        if error.name == "pyrogram":
            console.print("[red]Pyrogram is not installed. Run `pip install -r requirements.txt`.[/]")
            return 1
        console.print(f"[red]Unexpected error:[/] {error}")
        return 1
    except (FileNotFoundError, ValueError) as error:
        console.print(f"[red]{error}[/]")
        return 1
    except KeyboardInterrupt:
        console.print("[yellow]Upload cancelled by user.[/]")
        return 130
    except Exception as error:
        if error.__class__.__name__ == "FloodWait" and hasattr(error, "value"):
            console.print(f"[red]Telegram asked to wait {getattr(error, 'value')} seconds.[/]")
            return 1
        if error.__class__.__module__.startswith("pyrogram"):
            console.print(f"[red]Telegram RPC error:[/] {error}")
            return 1
        console.print(f"[red]Unexpected error:[/] {error}")
        return 1


def sync_main(argv: Optional[Sequence[str]] = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(sync_main())
