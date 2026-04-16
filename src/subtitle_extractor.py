#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, cast

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from yt_dlp import YoutubeDL


console = Console()
DEFAULT_OUTPUT_TYPE = "json"
OUTPUT_TYPES = ("srt", "json", "txt")
SUBTITLE_FORMAT_PREFERENCE = ("json3", "srv3", "srv2", "srv1", "ttml", "vtt", "srt")


@dataclass(frozen=True)
class SubtitleOption:
    language: str
    kind: str
    formats: List[Dict[str, Any]]


@dataclass(frozen=True)
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class SubtitleDocument:
    video_id: str
    title: str
    language: str
    kind: str
    source_format: str
    cues: List[SubtitleCue]


class QuietLogger:
    def debug(self, _: str) -> None:
        return

    def warning(self, message: str) -> None:
        if is_transient_network_warning(message):
            return
        console.print(f"[yellow]yt-dlp:[/] {message}")

    def error(self, message: str) -> None:
        console.print(f"[red]yt-dlp:[/] {message}")


class VerboseLogger:
    def debug(self, message: str) -> None:
        console.print(f"[dim]yt-dlp debug:[/] {message}")

    def warning(self, message: str) -> None:
        console.print(f"[yellow]yt-dlp warning:[/] {message}")

    def error(self, message: str) -> None:
        console.print(f"[red]yt-dlp error:[/] {message}")


def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        console.print(f"[dim][debug][/dim] {message}")


def is_transient_network_warning(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "retrying (",
        "unexpected_eof_while_reading",
        "ssl:",
    )
    return any(marker in lowered for marker in markers)


def print_banner() -> None:
    title = Text("Subtitle Extractor", style="bold bright_white")
    subtitle = Text(
        "Fetch subtitles with yt-dlp metadata, convert, and save as UTF-8", style="cyan"
    )
    console.print(
        Panel.fit(Text.assemble(title, "\n", subtitle), border_style="bright_blue")
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract subtitles from a YouTube video using yt-dlp metadata."
    )
    parser.add_argument("--url", help="Video URL")
    parser.add_argument("--id", help="Video ID")
    parser.add_argument("--lang", help="Subtitle language code, for example en or ar")
    parser.add_argument(
        "--type",
        dest="output_type",
        choices=OUTPUT_TYPES,
        default=DEFAULT_OUTPUT_TYPE,
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--output", help="Output file path (default: <video-id>.<type>)"
    )
    parser.add_argument("--cookies-file", help="Netscape cookies.txt file for yt-dlp")
    parser.add_argument("--browser", help="Browser name for yt-dlp cookie extraction")
    parser.add_argument(
        "--browser-profile", default=None, help="Browser profile name/path"
    )
    parser.add_argument(
        "--js-runtime",
        default="bun",
        help="yt-dlp JS runtime name to prefer (default: bun)",
    )
    parser.add_argument(
        "--js-runtime-path",
        default=None,
        help="Optional explicit path for the yt-dlp JS runtime",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print verbose debug logs for every step"
    )
    return parser.parse_args(argv)


def parse_target(args: argparse.Namespace) -> str:
    if args.url and args.url.strip():
        return args.url.strip()
    if args.id and args.id.strip():
        return f"https://www.youtube.com/watch?v={args.id.strip()}"
    value = Prompt.ask("Video URL or ID").strip()
    if not value:
        raise ValueError("A video URL or ID is required.")
    if re.match(r"^[A-Za-z0-9_-]{6,}$", value) and "://" not in value:
        return f"https://www.youtube.com/watch?v={value}"
    return value


def ydl_options(args: argparse.Namespace) -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "quiet": not args.debug,
        "no_warnings": not args.debug,
        "logger": VerboseLogger() if args.debug else QuietLogger(),
        "skip_download": True,
        "noplaylist": True,
    }
    js_runtime = build_js_runtime_option(
        args.js_runtime, args.js_runtime_path, args.debug
    )
    if js_runtime:
        options["js_runtimes"] = js_runtime
    if args.cookies_file:
        cookie_file = Path(args.cookies_file).expanduser()
        if not cookie_file.exists():
            raise FileNotFoundError(f"Cookies file not found: {cookie_file}")
        options["cookiefile"] = str(cookie_file)
    elif args.browser:
        options["cookiesfrombrowser"] = (
            args.browser.lower(),
            args.browser_profile,
            None,
            None,
        )
    return options


def build_js_runtime_option(
    runtime_name: Optional[str],
    runtime_path: Optional[str],
    debug: bool,
) -> Optional[Dict[str, Dict[str, str]]]:
    if not runtime_name or not runtime_name.strip():
        return None

    normalized_name = runtime_name.strip().lower()
    if normalized_name in {"none", "off", "disable", "disabled"}:
        debug_log(debug, "JS runtime override disabled")
        return None

    if runtime_path and runtime_path.strip():
        resolved_path = Path(runtime_path).expanduser()
        if not resolved_path.exists():
            raise FileNotFoundError(f"JS runtime path not found: {resolved_path}")
        debug_log(debug, f"Using JS runtime {normalized_name} from {resolved_path}")
        return {normalized_name: {"path": str(resolved_path)}}

    if shutil.which(normalized_name):
        debug_log(debug, f"Using JS runtime from PATH: {normalized_name}")
        return {normalized_name: {}}

    console.print(
        f"[yellow]{normalized_name} not found on PATH; continuing without a JS runtime override.[/]"
    )
    return None


def extract_video_info(target: str, args: argparse.Namespace) -> Dict[str, Any]:
    debug_log(args.debug, f"Extracting subtitle metadata for target={target}")
    with YoutubeDL(cast(Dict[str, Any], ydl_options(args))) as ydl:  # type: ignore[arg-type]
        info = cast(Dict[str, Any], ydl.extract_info(target, download=False))
    if info.get("_type") == "playlist":
        raise ValueError(
            "Please provide a single video URL or video ID, not a playlist."
        )
    return info


def collect_subtitle_options(info: Dict[str, Any]) -> List[SubtitleOption]:
    options: List[SubtitleOption] = []
    for kind, payload in (
        ("manual", info.get("subtitles") or {}),
        ("automatic", info.get("automatic_captions") or {}),
    ):
        if not isinstance(payload, dict):
            continue
        for language, formats in payload.items():
            if not (
                isinstance(language, str) and isinstance(formats, list) and formats
            ):
                continue
            direct_formats = [
                item
                for item in cast(List[Dict[str, Any]], formats)
                if isinstance(item.get("url"), str)
                and "tlang=" not in str(item.get("url"))
            ]
            if direct_formats:
                options.append(
                    SubtitleOption(language=language, kind=kind, formats=direct_formats)
                )
    return sorted(
        options,
        key=lambda item: (item.language.lower(), 0 if item.kind == "manual" else 1),
    )


def print_language_table(options: List[SubtitleOption]) -> None:
    table = Table(title="Available Subtitle Languages", header_style="bold bright_cyan")
    table.add_column("#", justify="right", style="bold yellow")
    table.add_column("Language", style="bold white")
    table.add_column("Kind", style="green")
    table.add_column("Formats", style="dim")
    for index, option in enumerate(options, start=1):
        formats = ", ".join(
            sorted({str(item.get("ext") or "unknown") for item in option.formats})
        )
        table.add_row(str(index), option.language, option.kind, formats)
    console.print(table)


def resolve_language_option(
    options: List[SubtitleOption],
    language: Optional[str],
) -> SubtitleOption:
    if not options:
        raise RuntimeError(
            "No directly downloadable subtitles were found for this video. YouTube may expose only translated captions for it."
        )

    if language and language.strip():
        normalized = language.strip().lower()
        exact_matches = [
            item for item in options if item.language.lower() == normalized
        ]
        if exact_matches:
            return exact_matches[0]
        prefix_matches = [
            item for item in options if item.language.lower().startswith(normalized)
        ]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        available = ", ".join(option.language for option in options)
        raise ValueError(
            f"Subtitle language `{language}` was not found among directly downloadable tracks. Available languages: {available}"
        )

    print_language_table(options)
    while True:
        choice = Prompt.ask("Language number or code").strip()
        if not choice:
            console.print("[yellow]Please choose a language.[/]")
            continue
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(options):
                return options[index - 1]
            console.print("[yellow]Invalid language number.[/]")
            continue
        try:
            return resolve_language_option(options, choice)
        except ValueError as error:
            console.print(f"[yellow]{error}[/]")


def choose_format(formats: Iterable[Dict[str, Any]]) -> str:
    best_ext = ""
    ranked = {name: index for index, name in enumerate(SUBTITLE_FORMAT_PREFERENCE)}
    best_rank = len(ranked) + 1
    for item in formats:
        ext = str(item.get("ext") or "").lower()
        rank = ranked.get(ext, len(ranked))
        if rank < best_rank:
            best_ext = ext or "unknown"
            best_rank = rank
    if not best_ext:
        raise RuntimeError(
            "No downloadable subtitle format was found for the selected language."
        )
    return best_ext


def download_subtitle_payload(
    info: Dict[str, Any], option: SubtitleOption, args: argparse.Namespace
) -> tuple[str, str]:
    source_format = choose_format(option.formats)
    with tempfile.TemporaryDirectory(prefix="toolsx-subtitles-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        download_options = cast(
            Dict[str, Any],
            {
                **ydl_options(args),
                "skip_download": True,
                "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
                "subtitleslangs": [option.language],
                "subtitlesformat": "/".join(SUBTITLE_FORMAT_PREFERENCE),
                "writesubtitles": option.kind == "manual",
                "writeautomaticsub": option.kind == "automatic",
            },
        )
        debug_log(
            args.debug,
            f"Downloading subtitle file via yt-dlp: lang={option.language} kind={option.kind} preferred={source_format}",
        )
        with YoutubeDL(download_options) as ydl:  # type: ignore[arg-type]
            ydl.download(
                [
                    str(
                        info.get("webpage_url")
                        or info.get("original_url")
                        or info.get("id")
                    )
                ]
            )

        matches = sorted(temp_dir.glob(f"{info.get('id')}.{option.language}.*"))
        if not matches:
            matches = sorted(temp_dir.glob(f"{info.get('id')}.*"))
        if not matches:
            raise RuntimeError(
                "yt-dlp did not create a subtitle file for the selected language."
            )

        subtitle_path = matches[0]
        detected_format = subtitle_path.suffix.lstrip(".").lower() or source_format
        debug_log(args.debug, f"Loaded subtitle file: {subtitle_path.name}")
        return detected_format, subtitle_path.read_text(
            encoding="utf-8", errors="replace"
        )


def clean_subtitle_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_time_value(value: str) -> float:
    raw = value.strip().replace(",", ".")
    if raw.endswith("s") and raw[:-1].replace(".", "", 1).isdigit():
        return float(raw[:-1])
    parts = raw.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    return float(raw)


def parse_vtt(content: str) -> List[SubtitleCue]:
    cues: List[SubtitleCue] = []
    lines = content.replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line == "WEBVTT" or line.startswith("NOTE"):
            index += 1
            continue
        if "-->" not in line:
            index += 1
            continue
        start_raw, end_raw = [
            part.strip().split(" ", 1)[0] for part in line.split("-->", 1)
        ]
        index += 1
        text_lines: List[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = clean_subtitle_text("\n".join(text_lines))
        if text:
            cues.append(
                SubtitleCue(
                    start_seconds=parse_time_value(start_raw),
                    end_seconds=parse_time_value(end_raw),
                    text=text,
                )
            )
        index += 1
    return cues


def parse_srt(content: str) -> List[SubtitleCue]:
    cues: List[SubtitleCue] = []
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        timing_line = lines[1] if "-->" in lines[1] else lines[0]
        if "-->" not in timing_line:
            continue
        start_raw, end_raw = [part.strip() for part in timing_line.split("-->", 1)]
        text_lines = lines[2:] if timing_line == lines[1] else lines[1:]
        text = clean_subtitle_text("\n".join(text_lines))
        if text:
            cues.append(
                SubtitleCue(
                    start_seconds=parse_time_value(start_raw),
                    end_seconds=parse_time_value(end_raw),
                    text=text,
                )
            )
    return cues


def parse_json3(content: str) -> List[SubtitleCue]:
    payload = json.loads(content)
    events = payload.get("events") or []
    cues: List[SubtitleCue] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs") or []
        if not isinstance(segs, list):
            continue
        text = clean_subtitle_text(
            "".join(str(seg.get("utf8") or "") for seg in segs if isinstance(seg, dict))
        )
        if not text:
            continue
        start_ms = int(event.get("tStartMs") or 0)
        duration_ms = int(event.get("dDurationMs") or 0)
        cues.append(
            SubtitleCue(
                start_seconds=start_ms / 1000,
                end_seconds=(start_ms + duration_ms) / 1000,
                text=text,
            )
        )
    return cues


def parse_ttml(content: str) -> List[SubtitleCue]:
    cues: List[SubtitleCue] = []
    root = ET.fromstring(content)
    for element in root.iter():
        if not element.tag.endswith("p"):
            continue
        begin = element.attrib.get("begin")
        end = element.attrib.get("end")
        if not begin or not end:
            continue
        text = clean_subtitle_text("".join(element.itertext()))
        if text:
            cues.append(
                SubtitleCue(
                    start_seconds=parse_time_value(begin),
                    end_seconds=parse_time_value(end),
                    text=text,
                )
            )
    return cues


def parse_subtitle_payload(content: str, source_format: str) -> List[SubtitleCue]:
    if source_format in {"json3", "srv1", "srv2", "srv3", "json"}:
        return parse_json3(content)
    if source_format == "ttml":
        return parse_ttml(content)
    if source_format == "srt":
        return parse_srt(content)
    return parse_vtt(content)


def format_srt_timestamp(seconds: float) -> str:
    milliseconds = max(int(round(seconds * 1000)), 0)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_document(document: SubtitleDocument, output_type: str) -> str:
    if output_type == "txt":
        lines: List[str] = []
        previous = None
        for cue in document.cues:
            if cue.text and cue.text != previous:
                lines.append(cue.text)
                previous = cue.text
        return "\n".join(lines).strip() + "\n"

    if output_type == "srt":
        blocks = []
        for index, cue in enumerate(document.cues, start=1):
            blocks.append(
                f"{index}\n"
                f"{format_srt_timestamp(cue.start_seconds)} --> {format_srt_timestamp(cue.end_seconds)}\n"
                f"{cue.text}"
            )
        return "\n\n".join(blocks).strip() + "\n"

    payload = {
        "video_id": document.video_id,
        "title": document.title,
        "language": document.language,
        "kind": document.kind,
        "source_format": document.source_format,
        "cue_count": len(document.cues),
        "cues": [
            {
                "start_seconds": cue.start_seconds,
                "end_seconds": cue.end_seconds,
                "text": cue.text,
            }
            for cue in document.cues
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def output_path_for(args: argparse.Namespace, video_id: str) -> Path:
    if args.output and args.output.strip():
        return Path(args.output).expanduser()
    return Path(f"{video_id}.{args.output_type}")


def print_video_summary(
    info: Dict[str, Any], selected: SubtitleOption, output_path: Path, output_type: str
) -> None:
    console.print(
        Panel(
            f"[bold]{info.get('title') or info.get('id') or 'Untitled'}[/]\n"
            f"[cyan]Video ID:[/] {info.get('id') or '-'}\n"
            f"[green]Language:[/] {selected.language}\n"
            f"[cyan]Source:[/] {selected.kind}\n"
            f"[green]Output type:[/] {output_type}\n"
            f"[cyan]Output file:[/] {output_path}",
            title="Extraction Plan",
            border_style="green",
        )
    )


def build_document(
    info: Dict[str, Any], option: SubtitleOption, args: argparse.Namespace
) -> SubtitleDocument:
    source_format, payload = download_subtitle_payload(info, option, args)
    cues = parse_subtitle_payload(payload, source_format)
    if not cues:
        raise RuntimeError(
            "Subtitle payload was downloaded, but no subtitle lines could be parsed."
        )
    return SubtitleDocument(
        video_id=str(info.get("id") or "unknown-video"),
        title=str(info.get("title") or info.get("id") or "Untitled"),
        language=option.language,
        kind=option.kind,
        source_format=source_format,
        cues=cues,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    print_banner()

    try:
        target = parse_target(args)
        info = extract_video_info(target, args)
        options = collect_subtitle_options(info)
        selected = resolve_language_option(options, args.lang)
        output_path = output_path_for(args, str(info.get("id") or "subtitle-output"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        print_video_summary(info, selected, output_path, args.output_type)

        document = build_document(info, selected, args)
        rendered = render_document(document, args.output_type)
        output_path.write_text(rendered, encoding="utf-8")

        console.print(
            Panel(
                f"[green]Saved {len(document.cues)} subtitle line(s)[/] to\n`{output_path}`",
                title="Done",
                border_style="bright_green",
            )
        )
        return 0
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        ET.ParseError,
    ) as error:
        console.print(f"[red]{error}[/]")
        return 1
    except KeyboardInterrupt:
        console.print("[yellow]Subtitle extraction cancelled by user.[/]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
