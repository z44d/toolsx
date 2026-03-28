#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, cast
from urllib.parse import parse_qs, urlparse

import requests
from mutagen.id3 import ID3
from mutagen.id3._frames import TXXX, USLT
from mutagen.id3._util import ID3NoHeaderError
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text
from ytmusicapi import YTMusic
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


console = Console()
DEFAULT_AUTH_FILE = Path("browser.json")
DEFAULT_OUTPUT_DIR = Path(".")
PRIVATE_ACCESS_DOCS = "https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
PLAYLIST_ID_PREFIXES = ("PL", "RDCLAK", "OLAK", "VL", "LM")


@dataclass
class PlaylistChoice:
    title: str
    playlist_id: str
    description: str
    count: str


@dataclass
class TrackInfo:
    video_id: str
    title: str
    artist: str
    album: str
    track_number: int
    duration_seconds: int
    thumbnail_url: Optional[str]
    lyrics_text: Optional[str] = None


@dataclass
class CookieSource:
    browser: str
    profile: Optional[str]


@dataclass
class DownloadAuth:
    cookie_source: Optional[CookieSource]
    cookie_file: Optional[Path]
    http_headers: Dict[str, str]


@dataclass
class TrackFailure:
    label: str
    message: str


@dataclass
class DownloadRunResult:
    downloaded_files: List[Path]
    downloaded_tracks: List[str]
    failures: List[TrackFailure]


@dataclass
class TargetSpec:
    kind: str
    identifier: str
    source_label: str


class QuietLogger:
    def debug(self, _: str) -> None:
        return

    def warning(self, message: str) -> None:
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


def sanitize_filename(value: str, max_length: int = 140) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length].rstrip(" .")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download YouTube Music songs or playlists as tagged MP3 files."
    )
    parser.add_argument(
        "--auth-file",
        default=str(DEFAULT_AUTH_FILE),
        help="Path to browser.json for private playlists/library access",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Base output directory (default: current directory, exported as ./[Album-Name])",
    )
    parser.add_argument("--url", help="Song or playlist URL")
    parser.add_argument("--id", help="Song video ID or playlist ID")
    parser.add_argument("--playlist-url")
    parser.add_argument("--playlist-id")
    parser.add_argument("--song-url")
    parser.add_argument("--song-id")
    parser.add_argument("--library-index", type=int)
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
        "--test-one", action="store_true", help="Download only the first song"
    )
    parser.add_argument(
        "--yes-all", action="store_true", help="Skip the first-song confirmation"
    )
    parser.add_argument(
        "--songs-limit",
        type=int,
        default=None,
        help="Maximum number of songs to download (default: all)",
    )
    parser.add_argument(
        "--lyrics-metadata",
        action="store_true",
        help="Fetch lyrics with timestamps and save them into song metadata",
    )
    parser.add_argument(
        "--keep-original-audio",
        action="store_true",
        help="Skip MP3 conversion and metadata tagging; keep the downloaded audio format",
    )
    parser.add_argument(
        "--mp3-bitrate",
        type=int,
        default=128,
        help="MP3 bitrate in kbps when conversion is enabled (default: 128)",
    )
    parser.add_argument("--zip", dest="zip_after", action="store_true")
    parser.add_argument("--no-zip", dest="zip_after", action="store_false")
    parser.add_argument(
        "--zip-max-size",
        type=int,
        help="Maximum source bytes per zip archive before splitting into parts",
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument(
        "--debug", action="store_true", help="Print verbose debug logs for every step"
    )
    parser.set_defaults(zip_after=None)
    args = parser.parse_args(argv)

    if args.songs_limit is not None and args.songs_limit < 1:
        parser.error("--songs-limit must be at least 1")
    if args.zip_max_size is not None and args.zip_max_size < 1:
        parser.error("--zip-max-size must be at least 1")
    if args.mp3_bitrate < 32:
        parser.error("--mp3-bitrate must be at least 32")
    return args


def maybe_binary(name: str) -> Optional[str]:
    return shutil.which(name)


def ensure_binary(name: str) -> str:
    path = maybe_binary(name)
    if not path:
        console.print(
            f"[red]Missing required dependency:[/] `{name}` is not available in PATH."
        )
        raise SystemExit(1)
    return path


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

    if maybe_binary(normalized_name):
        debug_log(debug, f"Using JS runtime from PATH: {normalized_name}")
        return {normalized_name: {}}

    console.print(
        f"[yellow]{normalized_name} not found on PATH; continuing without a JS runtime override.[/]"
    )
    return None


def header_lookup(data: Dict[str, Any], key: str) -> Optional[str]:
    key_lower = key.lower()
    for item_key, value in data.items():
        if item_key.lower() == key_lower and isinstance(value, str) and value.strip():
            return value
    return None


def build_cookie_file_from_auth(auth_file: Path) -> Optional[Path]:
    if not auth_file.exists():
        return None

    try:
        auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    cookie_header = header_lookup(auth_data, "cookie")
    if not cookie_header:
        return None

    cookie_jar = SimpleCookie()
    cookie_jar.load(cookie_header)
    if not cookie_jar:
        return None

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".cookies.txt", delete=False
    )
    with handle:
        handle.write("# Netscape HTTP Cookie File\n")
        for morsel in cookie_jar.values():
            secure = "TRUE" if morsel.key.startswith("__Secure-") else "FALSE"
            handle.write(
                f".youtube.com\tTRUE\t/\t{secure}\t0\t{morsel.key}\t{morsel.value}\n"
            )
    return Path(handle.name)


def resolve_download_auth(args: argparse.Namespace, auth_file: Path) -> DownloadAuth:
    cookie_file: Optional[Path] = None
    cookie_source: Optional[CookieSource] = None
    headers: Dict[str, str] = {}

    if auth_file.exists():
        try:
            auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            auth_data = {}

        for key, target in (
            ("user-agent", "User-Agent"),
            ("referer", "Referer"),
            ("origin", "Origin"),
        ):
            value = header_lookup(auth_data, key)
            if value:
                headers[target] = value

        cookie_file = build_cookie_file_from_auth(auth_file)

    if args.cookies_file:
        explicit_cookie_file = Path(args.cookies_file).expanduser()
        if not explicit_cookie_file.exists():
            console.print(f"[red]Cookies file not found:[/] `{explicit_cookie_file}`")
            raise SystemExit(1)
        cookie_file = explicit_cookie_file
        debug_log(args.debug, f"Using cookies file: {explicit_cookie_file}")

    if not cookie_file and args.browser:
        cookie_source = CookieSource(
            browser=args.browser.lower(), profile=args.browser_profile
        )
        debug_log(
            args.debug,
            f"Using browser cookies from {cookie_source.browser} profile={cookie_source.profile!r}",
        )

    if auth_file.exists():
        debug_log(args.debug, f"Loaded auth file: {auth_file}")

    return DownloadAuth(
        cookie_source=cookie_source, cookie_file=cookie_file, http_headers=headers
    )


def ensure_ytmusic(auth_file: Path) -> YTMusic:
    if auth_file.exists():
        return YTMusic(str(auth_file))
    return YTMusic()


def playlist_id_from_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme:
        return value.strip()
    playlist_id = parse_qs(parsed.query).get("list", [None])[0]
    if not playlist_id:
        raise ValueError("Playlist URL does not contain a list= parameter")
    return playlist_id


def song_id_from_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme:
        return value.strip()

    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/")

    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id

    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        return parts[-1]
    raise ValueError("Song URL does not contain a video id")


def is_playlist_identifier(value: str) -> bool:
    return value.startswith(PLAYLIST_ID_PREFIXES)


def infer_target_from_value(value: str) -> TargetSpec:
    parsed = urlparse(value)
    if parsed.scheme:
        query = parse_qs(parsed.query)
        if query.get("list"):
            return TargetSpec(
                kind="playlist",
                identifier=playlist_id_from_url(value),
                source_label="playlist URL",
            )
        return TargetSpec(
            kind="song", identifier=song_id_from_url(value), source_label="song URL"
        )

    stripped = value.strip()
    if is_playlist_identifier(stripped):
        return TargetSpec(
            kind="playlist", identifier=stripped, source_label="playlist ID"
        )
    return TargetSpec(kind="song", identifier=stripped, source_label="song ID")


def get_library_choices(ytmusic: YTMusic) -> List[PlaylistChoice]:
    items = ytmusic.get_library_playlists(limit=500)
    choices: List[PlaylistChoice] = []
    for item in items:
        playlist_id = item.get("playlistId") or ""
        if not playlist_id:
            continue
        choices.append(
            PlaylistChoice(
                title=item.get("title") or "Untitled Playlist",
                playlist_id=playlist_id,
                description=item.get("description") or "",
                count=item.get("count") or "?",
            )
        )
    return choices


def require_library_auth(auth_file: Path) -> None:
    if auth_file.exists():
        return
    console.print(
        Panel(
            "Library and private playlist access require a `browser.json` auth file.\n"
            f"Set it up with the ytmusicapi browser auth guide:\n{PRIVATE_ACCESS_DOCS}",
            title="Authentication Required",
            border_style="red",
        )
    )
    raise SystemExit(1)


def choose_target(
    args: argparse.Namespace, auth_file: Path, ytmusic: YTMusic
) -> TargetSpec:
    if args.song_id:
        return TargetSpec(kind="song", identifier=args.song_id, source_label="song ID")
    if args.song_url:
        return TargetSpec(
            kind="song",
            identifier=song_id_from_url(args.song_url),
            source_label="song URL",
        )
    if args.playlist_id:
        return TargetSpec(
            kind="playlist", identifier=args.playlist_id, source_label="playlist ID"
        )
    if args.playlist_url:
        return TargetSpec(
            kind="playlist",
            identifier=playlist_id_from_url(args.playlist_url),
            source_label="playlist URL",
        )
    if args.url:
        return infer_target_from_value(args.url)
    if args.id:
        return infer_target_from_value(args.id)
    if args.library_index is not None:
        require_library_auth(auth_file)
        choices = get_library_choices(ytmusic)
        if args.library_index < 1 or args.library_index > len(choices):
            console.print("[red]Library index is out of range.[/]")
            raise SystemExit(1)
        return TargetSpec(
            kind="playlist",
            identifier=choices[args.library_index - 1].playlist_id,
            source_label="library",
        )

    if args.non_interactive:
        console.print(
            "[red]Non-interactive mode needs a song/playlist URL or ID, or --library-index for library access.[/]"
        )
        raise SystemExit(1)

    console.print(
        Panel(
            "Paste a YouTube Music / YouTube song URL, playlist URL, song ID, or playlist ID.\n"
            "If you already have `browser.json`, you can also pick from your private library.",
            title="Source Picker",
            border_style="magenta",
        )
    )

    choices = ["url-or-id"]
    if auth_file.exists():
        choices.append("library")
    mode = Prompt.ask("Source", choices=choices, default=choices[0])
    if mode == "library":
        require_library_auth(auth_file)
        library_choices = get_library_choices(ytmusic)
        if not library_choices:
            console.print("[red]No library playlists found.[/]")
            raise SystemExit(1)
        table = Table(show_header=True, header_style="bold bright_cyan")
        table.add_column("#", justify="right", style="bold yellow")
        table.add_column("Playlist", style="bold white")
        table.add_column("Tracks", justify="right", style="green")
        table.add_column("Description", style="dim")
        for index, choice in enumerate(library_choices, start=1):
            table.add_row(str(index), choice.title, choice.count, choice.description)
        console.print(table)
        selection = IntPrompt.ask("Playlist number", default=1)
        if selection < 1 or selection > len(library_choices):
            console.print("[red]Invalid playlist selection.[/]")
            raise SystemExit(1)
        chosen = library_choices[selection - 1]
        return TargetSpec(
            kind="playlist", identifier=chosen.playlist_id, source_label="library"
        )

    raw_value = Prompt.ask("Song or playlist URL / ID")
    return infer_target_from_value(raw_value)


def get_playlist_data(ytmusic: YTMusic, playlist_id: str) -> Dict[str, Any]:
    if playlist_id == "LM":
        return ytmusic.get_liked_songs(limit=5000)
    return ytmusic.get_playlist(playlist_id, limit=5000)


def build_single_track_seed(ytmusic: YTMusic, video_id: str) -> Dict[str, Any]:
    watch = ytmusic.get_watch_playlist(videoId=video_id, limit=1)
    watch_tracks = cast(List[Dict[str, Any]], watch.get("tracks") or [])
    if watch_tracks:
        return watch_tracks[0]

    details = ytmusic.get_song(video_id)
    video_details = details.get("videoDetails", {})
    author = video_details.get("author")
    return {
        "videoId": video_id,
        "title": video_details.get("title") or video_id,
        "artists": [{"name": author}] if author else [],
        "duration_seconds": int(video_details.get("lengthSeconds") or 0),
        "isAvailable": True,
    }


def get_song_collection(ytmusic: YTMusic, video_id: str) -> Dict[str, Any]:
    track = build_single_track_seed(ytmusic, video_id)
    title = track.get("title") or video_id
    return {
        "title": title,
        "description": "Single song",
        "duration": track.get("duration") or "1 track",
        "tracks": [track],
    }


def best_thumbnail_url(thumbnails: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    if not thumbnails:
        return None
    best = max(
        thumbnails, key=lambda item: item.get("width", 0) * item.get("height", 0)
    )
    url = best.get("url")
    if not url:
        return None
    upgraded = re.sub(r"=w\d+-h\d+[^&]*", "=w1200-h1200-l90-rj", url)
    upgraded = re.sub(r"=s\d+[^&]*", "=w1200-h1200-l90-rj", upgraded)
    return upgraded


def extract_thumbnail_list(value: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(value, list):
        return cast(List[Dict[str, Any]], value)
    if isinstance(value, dict):
        thumbnails = value.get("thumbnails")
        if isinstance(thumbnails, list):
            return cast(List[Dict[str, Any]], thumbnails)
    return None


def format_timestamp(milliseconds: int) -> str:
    total_seconds = max(milliseconds, 0) / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"[{minutes:02d}:{seconds:05.2f}]"


def fetch_lyrics_text(ytmusic: YTMusic, video_id: str, debug: bool) -> Optional[str]:
    try:
        debug_log(debug, f"Fetching lyrics browse id for {video_id}")
        watch = ytmusic.get_watch_playlist(videoId=video_id, limit=1)
        lyrics_browse_id = watch.get("lyrics")
        if not isinstance(lyrics_browse_id, str) or not lyrics_browse_id:
            debug_log(debug, f"No lyrics browse id found for {video_id}")
            return None
        debug_log(
            debug, f"Fetching lyrics payload for {video_id} browseId={lyrics_browse_id}"
        )
        lyrics_payload = ytmusic.get_lyrics(lyrics_browse_id, timestamps=True)
    except Exception as exc:
        debug_log(debug, f"Lyrics fetch failed for {video_id}: {exc}")
        return None

    if not lyrics_payload:
        debug_log(debug, f"Lyrics payload empty for {video_id}")
        return None

    source = lyrics_payload.get("source")
    if lyrics_payload.get("hasTimestamps"):
        lines = []
        for line in lyrics_payload.get("lyrics", []):
            text = getattr(line, "text", "") or ""
            start_time = int(getattr(line, "start_time", 0) or 0)
            if text:
                lines.append(f"{format_timestamp(start_time)} {text}")
        text = "\n".join(lines)
    else:
        text = str(lyrics_payload.get("lyrics") or "").strip()

    if not text:
        debug_log(debug, f"Lyrics text empty for {video_id}")
        return None
    if source:
        text = f"{text}\n\n{source}"
    debug_log(debug, f"Lyrics ready for {video_id}: {len(text)} chars")
    return text


def build_track_info(
    ytmusic: YTMusic,
    raw_track: Dict[str, Any],
    index: int,
    collection_title: str,
    include_lyrics: bool,
    debug: bool,
) -> TrackInfo:
    video_id = raw_track.get("videoId")
    if not video_id:
        raise ValueError("Track does not have a videoId")

    details: Dict[str, Any] = {}
    try:
        details = ytmusic.get_song(video_id)
        debug_log(debug, f"Fetched song details for {video_id}")
    except Exception as exc:
        debug_log(debug, f"Failed to fetch song details for {video_id}: {exc}")
        details = {}

    artists = raw_track.get("artists") or []
    artist_names = [artist.get("name") for artist in artists if artist.get("name")]
    if not artist_names:
        if details.get("author"):
            artist_names = [details["author"]]
        elif raw_track.get("artist"):
            artist_names = [raw_track["artist"]]
        elif details.get("videoDetails", {}).get("author"):
            artist_names = [details["videoDetails"]["author"]]

    album = ""
    if isinstance(raw_track.get("album"), dict):
        album = raw_track["album"].get("name") or ""
    elif isinstance(raw_track.get("album"), str):
        album = raw_track.get("album") or ""
    if not album:
        microformat = details.get("microformat", {}).get("microformatDataRenderer", {})
        album = microformat.get("album") or ""

    thumbnail_candidates = [
        extract_thumbnail_list(details.get("thumbnail")),
        extract_thumbnail_list(details.get("videoDetails", {}).get("thumbnail")),
        extract_thumbnail_list(
            details.get("microformat", {})
            .get("microformatDataRenderer", {})
            .get("thumbnail")
        ),
        extract_thumbnail_list(raw_track.get("thumbnails")),
        extract_thumbnail_list(raw_track.get("thumbnail")),
    ]
    thumbnail_url = best_thumbnail_url(
        next((item for item in thumbnail_candidates if item), None)
    )
    debug_log(debug, f"Thumbnail for {video_id}: {thumbnail_url or 'none'}")

    return TrackInfo(
        video_id=video_id,
        title=raw_track.get("title") or details.get("title") or f"Track {index}",
        artist=", ".join(artist_names) or "Unknown Artist",
        album=album or collection_title,
        track_number=index,
        duration_seconds=int(
            raw_track.get("duration_seconds")
            or details.get("lengthSeconds")
            or details.get("videoDetails", {}).get("lengthSeconds")
            or 0
        ),
        thumbnail_url=thumbnail_url,
        lyrics_text=fetch_lyrics_text(ytmusic, video_id, debug)
        if include_lyrics
        else None,
    )


def fetch_thumbnail(
    url: Optional[str], temp_dir: Path, video_id: str, debug: bool
) -> Optional[Path]:
    if not url:
        debug_log(debug, f"No thumbnail URL for {video_id}")
        return None
    try:
        debug_log(debug, f"Downloading thumbnail for {video_id} from {url}")
        response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        console.print(f"[yellow]Skipping artwork for {video_id}:[/] {exc}")
        debug_log(debug, f"Thumbnail download failed for {video_id}: {exc}")
        return None

    path = temp_dir / f"{video_id}.jpg"
    path.write_bytes(response.content)
    debug_log(debug, f"Thumbnail saved for {video_id}: {path}")
    return path


def ensure_track_in_archive(archive_path: Path, track: TrackInfo) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"youtube {track.video_id}\n"
    if archive_path.exists() and line in archive_path.read_text(
        encoding="utf-8", errors="ignore"
    ).splitlines(keepends=True):
        return
    with archive_path.open("a", encoding="utf-8") as archive_file:
        archive_file.write(line)


def download_track(
    track: TrackInfo,
    folder: Path,
    js_runtime: Optional[Dict[str, Dict[str, str]]],
    download_auth: DownloadAuth,
    archive_path: Path,
    progress: Progress,
    overall_task: TaskID,
    keep_original_audio: bool,
    mp3_bitrate: int,
    debug: bool,
) -> Path:
    stem = sanitize_filename(
        f"{track.track_number:03d} - {track.title} [{track.video_id}]"
    )
    target = folder / (f"{stem}.audio" if keep_original_audio else f"{stem}.mp3")

    if not keep_original_audio and target.exists():
        ensure_track_in_archive(archive_path, track)
        progress.advance(overall_task, 1)
        return target

    if keep_original_audio:
        existing_matches = sorted(folder.glob(f"*{track.video_id}*.*"))
        media_matches = [
            path
            for path in existing_matches
            if path.suffix.lower() not in {".txt", ".jpg", ".jpeg", ".png"}
        ]
        if media_matches:
            debug_log(
                debug,
                f"Reusing existing media file for {track.video_id}: {media_matches[0]}",
            )
            ensure_track_in_archive(archive_path, track)
            progress.advance(overall_task, 1)
            return media_matches[0]

    current_task = progress.add_task(
        f"[cyan]Downloading[/] {track.track_number:03d}. {track.title[:40]}",
        total=None,
    )

    def hook(data: Dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            progress.update(current_task, total=total, completed=downloaded)
            if debug:
                console.print(
                    f"[dim]yt-dlp progress {track.video_id}: {downloaded}/{total or '?'} bytes[/]"
                )
        elif status == "finished":
            total = data.get("total_bytes") or data.get("downloaded_bytes") or 1
            progress.update(current_task, total=total, completed=total)
            debug_log(debug, f"yt-dlp finished download phase for {track.video_id}")

    options = cast(
        Dict[str, Any],
        {
            "noplaylist": True,
            "quiet": not debug,
            "no_warnings": not debug,
            "logger": VerboseLogger() if debug else QuietLogger(),
            "outtmpl": {"default": str(folder / f"{stem}.%(ext)s")},
            "progress_hooks": [hook],
            "download_archive": str(archive_path),
            "format": "bestaudio[abr<=140]/bestaudio/best",
            "verbose": debug,
        },
    )

    if not keep_original_audio:
        options["final_ext"] = "mp3"
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(mp3_bitrate),
            }
        ]

    if js_runtime:
        options["js_runtimes"] = js_runtime
    if download_auth.cookie_file:
        options["cookiefile"] = str(download_auth.cookie_file)
    elif download_auth.cookie_source:
        options["cookiesfrombrowser"] = (
            download_auth.cookie_source.browser,
            download_auth.cookie_source.profile,
            None,
            None,
        )
    if download_auth.http_headers:
        options["http_headers"] = download_auth.http_headers

    try:
        debug_log(
            debug,
            f"Starting yt-dlp for {track.video_id} keep_original_audio={keep_original_audio} mp3_bitrate={mp3_bitrate}",
        )
        with YoutubeDL(options) as ydl:  # type: ignore[arg-type]
            ydl.extract_info(
                f"https://www.youtube.com/watch?v={track.video_id}", download=True
            )
    except DownloadError as exc:
        raise RuntimeError(f"Failed to download {track.title}: {exc}") from exc
    finally:
        progress.remove_task(current_task)

    if not target.exists():
        pattern = (
            f"*{track.video_id}*.*"
            if keep_original_audio
            else f"*{track.video_id}*.mp3"
        )
        matches = sorted(folder.glob(pattern))
        if keep_original_audio:
            matches = [
                path
                for path in matches
                if path.suffix.lower() not in {".txt", ".jpg", ".jpeg", ".png"}
            ]
        if matches:
            target = matches[0]
    if not target.exists():
        raise RuntimeError(f"yt-dlp completed but `{target.name}` was not created")

    debug_log(debug, f"Resolved output file for {track.video_id}: {target}")
    ensure_track_in_archive(archive_path, track)
    progress.advance(overall_task, 1)
    return target


def write_lyrics_metadata(mp3_path: Path, lyrics_text: Optional[str]) -> None:
    if not lyrics_text:
        return

    try:
        tags = ID3(str(mp3_path))
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("USLT")
    tags.delall("TXXX:TOOLSX_LYRICS_FORMAT")
    tags.add(USLT(encoding=3, lang="eng", desc="Lyrics", text=lyrics_text))
    tags.add(TXXX(encoding=3, desc="TOOLSX_LYRICS_FORMAT", text=["timestamped"]))
    tags.save(str(mp3_path), v2_version=3)


def format_track_label(track: TrackInfo) -> str:
    return f"{track.track_number:03d}. {track.title} - {track.artist}"


def is_unavailable_error(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "video unavailable",
        "this video is not available",
        "private video",
        "video has been removed",
        "sign in to confirm your age",
    )
    return any(marker in lowered for marker in markers)


def print_run_summary(result: DownloadRunResult) -> None:
    if result.downloaded_tracks:
        console.print(
            Panel(
                "\n".join(result.downloaded_tracks),
                title="Downloaded Songs",
                border_style="green",
            )
        )

    if result.failures:
        console.print(
            Panel(
                "\n".join(
                    f"{failure.label}: {failure.message}" for failure in result.failures
                ),
                title="Failed to Download",
                border_style="red",
            )
        )


def process_tracks(
    ytmusic: YTMusic,
    collection: Dict[str, Any],
    raw_tracks: List[Dict[str, Any]],
    start_index: int,
    playlist_folder: Path,
    js_runtime: Optional[Dict[str, Dict[str, str]]],
    download_auth: DownloadAuth,
    archive_path: Path,
    include_lyrics: bool,
    keep_original_audio: bool,
    mp3_bitrate: int,
    debug: bool,
) -> DownloadRunResult:
    downloaded_files: List[Path] = []
    downloaded_tracks: List[str] = []
    failures: List[TrackFailure] = []

    with tempfile.TemporaryDirectory(prefix="ytm-artwork-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)

        with (
            Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=36),
                TaskProgressColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as download_progress,
            Progress(
                SpinnerColumn(style="magenta"),
                TextColumn("[bold magenta]{task.description}"),
                BarColumn(bar_width=36),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as metadata_progress,
        ):
            download_overall = download_progress.add_task(
                "Downloading tracks", total=len(raw_tracks)
            )
            metadata_overall = metadata_progress.add_task(
                "Updating metadata", total=len(raw_tracks)
            )

            for offset, raw_track in enumerate(raw_tracks, start=start_index):
                track: Optional[TrackInfo] = None
                try:
                    track = build_track_info(
                        ytmusic,
                        raw_track,
                        offset,
                        collection.get("title") or "Collection",
                        include_lyrics,
                        debug,
                    )
                    assert track is not None
                    debug_log(
                        debug,
                        f"Prepared track {track.video_id}: title={track.title!r} album={track.album!r}",
                    )
                    mp3_path = download_track(
                        track,
                        playlist_folder,
                        js_runtime,
                        download_auth,
                        archive_path,
                        download_progress,
                        download_overall,
                        keep_original_audio,
                        mp3_bitrate,
                        debug,
                    )
                    if keep_original_audio:
                        debug_log(
                            debug,
                            f"Skipping metadata/tagging for {track.video_id} because keep-original-audio is enabled",
                        )
                        metadata_progress.advance(metadata_overall, 1)
                    else:
                        thumbnail_path = fetch_thumbnail(
                            track.thumbnail_url, temp_dir, track.video_id, debug
                        )
                        apply_metadata(
                            track,
                            mp3_path,
                            thumbnail_path,
                            metadata_progress,
                            metadata_overall,
                            debug,
                        )
                        debug_log(debug, f"Applied metadata for {track.video_id}")
                    downloaded_files.append(mp3_path)
                    downloaded_tracks.append(format_track_label(track))
                except Exception as exc:
                    label = (
                        format_track_label(track)
                        if track
                        else str(raw_track.get("title") or f"Track {offset}")
                    )
                    failures.append(TrackFailure(label=label, message=str(exc)))
                    download_progress.advance(download_overall, 1)
                    metadata_progress.advance(metadata_overall, 1)

    return DownloadRunResult(
        downloaded_files=downloaded_files,
        downloaded_tracks=downloaded_tracks,
        failures=failures,
    )


def apply_metadata(
    track: TrackInfo,
    mp3_path: Path,
    thumbnail_path: Optional[Path],
    progress: Progress,
    overall_task: TaskID,
    debug: bool = False,
) -> None:
    current_task = progress.add_task(
        f"[magenta]Tagging[/] {track.track_number:03d}. {track.title[:40]}",
        total=max(track.duration_seconds, 1),
    )

    temp_output = mp3_path.with_name(f"{mp3_path.stem}.tagged.mp3")
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(mp3_path),
    ]

    if thumbnail_path:
        command.extend(
            ["-i", str(thumbnail_path), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg"]
        )
    else:
        command.extend(["-map", "0:a"])

    command.extend(
        [
            "-c:a",
            "copy",
            "-id3v2_version",
            "3",
            "-metadata",
            f"title={track.title}",
            "-metadata",
            f"artist={track.artist}",
            "-metadata",
            f"album={track.album}",
            "-metadata",
            f"track={track.track_number}",
        ]
    )

    if thumbnail_path:
        command.extend(
            [
                "-metadata:s:v",
                "title=Album cover",
                "-metadata:s:v",
                "comment=Cover (front)",
                "-disposition:v:0",
                "attached_pic",
            ]
        )

    command.extend(["-progress", "pipe:1", "-nostats", str(temp_output)])

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    debug_log(debug, f"Running ffmpeg metadata command for {track.video_id}")
    assert process.stdout is not None

    for line in process.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            out_ms = int(line.split("=", 1)[1] or 0)
            completed = min(track.duration_seconds, out_ms / 1_000_000)
            progress.update(current_task, completed=completed)
        elif line == "progress=end":
            progress.update(current_task, completed=max(track.duration_seconds, 1))

    stderr_output = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    progress.remove_task(current_task)

    if return_code != 0:
        temp_output.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg metadata update failed for {track.title}: {stderr_output.strip()}"
        )

    temp_output.replace(mp3_path)
    write_lyrics_metadata(mp3_path, track.lyrics_text)
    debug_log(
        debug,
        f"Metadata written for {track.video_id}; lyrics={'yes' if track.lyrics_text else 'no'}",
    )
    progress.advance(overall_task, 1)


def visible_files(folder: Path) -> List[Path]:
    return [
        path
        for path in folder.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    ]


def chunk_files(files: Iterable[Path], max_size: Optional[int]) -> List[List[Path]]:
    all_files = list(files)
    if not max_size:
        return [all_files]

    chunks: List[List[Path]] = []
    current_chunk: List[Path] = []
    current_size = 0

    for path in all_files:
        file_size = path.stat().st_size
        if current_chunk and current_size + file_size > max_size:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(path)
        current_size += file_size

    if current_chunk:
        chunks.append(current_chunk)
    return chunks or [[]]


def archive_name_for_part(
    base_zip_path: Path, part_index: int, total_parts: int
) -> Path:
    if total_parts <= 1:
        return base_zip_path
    return base_zip_path.with_name(
        f"{base_zip_path.stem}.part{part_index:02d}{base_zip_path.suffix}"
    )


def zip_files(folder: Path, zip_path: Path, files: List[Path]) -> None:
    total_bytes = sum(path.stat().st_size for path in files) or 1
    with Progress(
        SpinnerColumn(style="yellow"),
        TextColumn("[bold yellow]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Creating {zip_path.name}", total=total_bytes)
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in files:
                archive.write(path, arcname=path.relative_to(folder.parent))
                progress.advance(task, path.stat().st_size)


def zip_folder(folder: Path, zip_path: Path, max_size: Optional[int]) -> List[Path]:
    files = visible_files(folder)
    if not files:
        return []

    chunks = chunk_files(files, max_size)
    created_archives: List[Path] = []
    for part_index, chunk in enumerate(chunks, start=1):
        part_path = archive_name_for_part(zip_path, part_index, len(chunks))
        zip_files(folder, part_path, chunk)
        created_archives.append(part_path)
    return created_archives


def print_banner() -> None:
    title = Text("YTMusic Downloader", style="bold bright_white")
    subtitle = Text(
        "Public URLs, private library auth, tagged MP3 export", style="cyan"
    )
    console.print(
        Panel.fit(Text.assemble(title, "\n", subtitle), border_style="bright_blue")
    )


def summarize_collection(
    target: TargetSpec, collection: Dict[str, Any], tracks: List[Dict[str, Any]]
) -> None:
    title = collection.get("title") or "Untitled"
    description = collection.get("description") or (
        "Single song" if target.kind == "song" else "No description"
    )
    duration = collection.get("duration") or f"{len(tracks)} track(s)"
    console.print(
        Panel(
            f"[bold]{title}[/]\n[dim]{description}[/]\n\n"
            f"[cyan]Source:[/] {target.source_label}\n"
            f"[green]Tracks:[/] {len(tracks)}\n"
            f"[cyan]Duration:[/] {duration}",
            title="Selected Source",
            border_style="green",
        )
    )


def finish_run(
    args: argparse.Namespace,
    result: DownloadRunResult,
    export_folder: Path,
) -> int:
    print_run_summary(result)

    zip_after = args.zip_after
    if zip_after is None and not args.non_interactive:
        zip_after = Confirm.ask(
            "Compress the downloaded files into zip archives?", default=False
        )
    if zip_after:
        base_zip_path = export_folder.with_suffix(".zip")
        archives = zip_folder(export_folder, base_zip_path, args.zip_max_size)
        if archives:
            console.print(
                Panel(
                    "\n".join(str(path) for path in archives),
                    title="Created Archives",
                    border_style="yellow",
                )
            )

    console.print(
        Panel(
            f"[bold green]Done.[/] Exported [bold]{len(result.downloaded_files)}[/] track(s) to\n`{export_folder}`",
            title="Finished",
            border_style="bright_green",
        )
    )
    return 0 if not result.failures else 1


def run_remaining(
    args: argparse.Namespace,
    ytmusic: YTMusic,
    collection: Dict[str, Any],
    remaining_tracks: List[Dict[str, Any]],
    export_folder: Path,
    js_runtime: Optional[Dict[str, Dict[str, str]]],
    download_auth: DownloadAuth,
    archive_path: Path,
    include_lyrics: bool,
    keep_original_audio: bool,
    mp3_bitrate: int,
    debug: bool,
    prior_result: Optional[DownloadRunResult] = None,
    start_index: int = 2,
) -> int:
    result = process_tracks(
        ytmusic,
        collection,
        remaining_tracks,
        start_index,
        export_folder,
        js_runtime,
        download_auth,
        archive_path,
        include_lyrics,
        keep_original_audio,
        mp3_bitrate,
        debug,
    )

    combined_result = DownloadRunResult(
        downloaded_files=[
            *(prior_result.downloaded_files if prior_result else []),
            *result.downloaded_files,
        ],
        downloaded_tracks=[
            *(prior_result.downloaded_tracks if prior_result else []),
            *result.downloaded_tracks,
        ],
        failures=[*(prior_result.failures if prior_result else []), *result.failures],
    )
    return finish_run(args, combined_result, export_folder)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    print_banner()

    ffmpeg_path = ensure_binary("ffmpeg")
    js_runtime = build_js_runtime_option(
        args.js_runtime,
        args.js_runtime_path,
        args.debug,
    )
    _ = ffmpeg_path

    auth_file = Path(args.auth_file).expanduser()
    output_root = Path(args.output_dir).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)

    ytmusic = ensure_ytmusic(auth_file)
    download_auth = resolve_download_auth(args, auth_file)
    target = choose_target(args, auth_file, ytmusic)
    debug_log(
        args.debug,
        f"Selected target: kind={target.kind} id={target.identifier} source={target.source_label}",
    )

    if target.kind == "playlist":
        collection = get_playlist_data(ytmusic, target.identifier)
    else:
        collection = get_song_collection(ytmusic, target.identifier)

    tracks = [
        track
        for track in collection.get("tracks", [])
        if track.get("videoId") and track.get("isAvailable", True)
    ]
    if args.songs_limit is not None:
        tracks = tracks[: args.songs_limit]

    if not tracks:
        console.print("[red]No downloadable tracks were found for this source.[/]")
        return 1

    summarize_collection(target, collection, tracks)
    export_name = sanitize_filename(collection.get("title") or target.identifier)
    export_folder = output_root / export_name
    export_folder.mkdir(parents=True, exist_ok=True)
    archive_path = export_folder / ".download-archive.txt"

    if args.test_one:
        initial_tracks = tracks[:1]
    elif (
        args.yes_all
        or args.non_interactive
        or len(tracks) == 1
        or target.kind == "song"
    ):
        initial_tracks = tracks
    else:
        console.print(
            "[bold cyan]Safety check:[/] downloading the first song before the full run."
        )
        initial_tracks = tracks[:1]

    initial_result = process_tracks(
        ytmusic,
        collection,
        initial_tracks,
        1,
        export_folder,
        js_runtime,
        download_auth,
        archive_path,
        args.lyrics_metadata,
        args.keep_original_audio,
        args.mp3_bitrate,
        args.debug,
    )

    if not initial_result.downloaded_files:
        should_skip_failed_safety_track = (
            not args.test_one
            and not args.yes_all
            and not args.non_interactive
            and len(tracks) > 1
            and len(initial_tracks) == 1
            and initial_result.failures
            and all(
                is_unavailable_error(failure.message)
                for failure in initial_result.failures
            )
        )
        if should_skip_failed_safety_track:
            console.print(
                "[yellow]Safety check hit an unavailable song. Skipping it and continuing.[/]"
            )
            return run_remaining(
                args,
                ytmusic,
                collection,
                tracks[1:],
                export_folder,
                js_runtime,
                download_auth,
                archive_path,
                args.lyrics_metadata,
                args.keep_original_audio,
                args.mp3_bitrate,
                args.debug,
                initial_result,
                start_index=2,
            )

        print_run_summary(initial_result)
        console.print("[red]Nothing was downloaded successfully.[/]")
        return 1

    if (
        len(initial_tracks) == 1
        and len(tracks) > 1
        and not (args.test_one or args.yes_all or args.non_interactive)
    ):
        remaining_tracks = tracks[1:]
        continue_full = Confirm.ask(
            f"The first song finished. Continue with the remaining {len(remaining_tracks)} tracks?",
            default=True,
        )
        if continue_full:
            console.print("[green]Resuming full download...[/]")
            return run_remaining(
                args,
                ytmusic,
                collection,
                remaining_tracks,
                export_folder,
                js_runtime,
                download_auth,
                archive_path,
                args.lyrics_metadata,
                args.keep_original_audio,
                args.mp3_bitrate,
                args.debug,
                initial_result,
            )

    return finish_run(args, initial_result, export_folder)


if __name__ == "__main__":
    raise SystemExit(main())
