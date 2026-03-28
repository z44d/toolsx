from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    key: str
    command: str
    module: str
    callable_name: str
    summary: str


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="ytm_dl",
        command="ytm-dl",
        module="ytm_dl",
        callable_name="main",
        summary="Download YouTube Music songs or playlists as tagged MP3 files.",
    ),
    ToolSpec(
        key="tg_uploader",
        command="tg-uploader",
        module="tg_uploader",
        callable_name="sync_main",
        summary="Upload files to Telegram with bot credentials from args, env, or prompts.",
    ),
    ToolSpec(
        key="subtitle_extractor",
        command="subtitle-extract",
        module="subtitle_extractor",
        callable_name="main",
        summary="Extract subtitles with yt-dlp metadata and save them as UTF-8 JSON, SRT, or text.",
    ),
)


def find_tool(name: str) -> ToolSpec | None:
    for tool in TOOLS:
        if name in {tool.key, tool.command}:
            return tool
    return None
