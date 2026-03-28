# toolsx

`toolsx` packages a small CLI toolbox with a few ready-to-use commands:

- `ytm-dl` - download a single YouTube Music song or a full playlist as tagged MP3 files.
- `tg-uploader` - upload a file to Telegram with a bot session.
- `subtitle-extract` - extract video subtitles or auto-captions as UTF-8 JSON, SRT, or text.
- `toolsx` - list the installed tools and dispatch to a tool by name.

## Install

```bash
pip install tools_extra
```

The package is published as `tools_extra`, but the installed CLI commands stay `toolsx`, `ytm-dl`, and `tg-uploader`.

From the repo:

```bash
python -m pip install .
```

## Commands

Running `toolsx` prints the available tools:

```bash
toolsx
```

You can also dispatch through the umbrella command:

```bash
toolsx ytm-dl --help
toolsx tg-uploader --help
toolsx subtitle-extract --help
```

Direct commands stay available too:

```bash
ytm-dl --help
tg-uploader --help
subtitle-extract --help
```

## subtitle-extract

`subtitle-extract` uses `yt-dlp` metadata to inspect available subtitles, lets you choose a language when needed, and writes UTF-8 output as `json`, `srt`, or `txt`.

Examples:

```bash
subtitle-extract --url "https://www.youtube.com/watch?v=VIDEO_ID"
subtitle-extract --id VIDEO_ID --lang en --type srt
subtitle-extract --url "https://www.youtube.com/watch?v=VIDEO_ID" --lang ar --type txt --output ./captions.txt
subtitle-extract --url "https://www.youtube.com/watch?v=VIDEO_ID" --cookies-file ./cookies.txt --debug
subtitle-extract --url "https://www.youtube.com/watch?v=VIDEO_ID" --browser chrome --browser-profile Default
subtitle-extract --url "https://www.youtube.com/watch?v=VIDEO_ID" --js-runtime bun --js-runtime-path /opt/homebrew/bin/bun
```

- `--lang` skips the interactive language picker.
- `--type` defaults to `json`.
- `--output` defaults to `<video-id>.<type>`.
- `--cookies-file` and `--browser` support videos that need authenticated subtitle access.
- `--js-runtime` defaults to `bun`; use `--js-runtime-path` to point yt-dlp at a specific runtime binary.
- If `--lang` is omitted, the tool shows available languages and prompts you to choose one.

## ytm-dl

### Public song or playlist

No `browser.json` file is required for public URLs or IDs.

```bash
ytm-dl --url "https://music.youtube.com/playlist?list=PL..."
ytm-dl --url "https://music.youtube.com/watch?v=VIDEO_ID"
ytm-dl --id VIDEO_ID
```

### Private playlists or library access

For private playlists, liked songs, or library selection, create `browser.json` first with the ytmusicapi browser auth guide:

- https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html

Then run:

```bash
ytm-dl --auth-file browser.json --library-index 1
```

### Useful flags

```bash
ytm-dl \
  --url "https://music.youtube.com/playlist?list=PL..." \
  --output-dir ./exports \
  --yes-all \
  --songs-limit 25 \
  --lyrics-metadata \
  --zip \
  --zip-max-size 2000000000
```

- `--cookies-file` uses a `cookies.txt` file instead of browser cookie extraction.
- `--browser` and `--browser-profile` use cookies directly from a browser when needed.
- `--js-runtime` defaults to `bun`; `--js-runtime-path` lets yt-dlp use an explicit runtime binary path.
- `--yes-all` skips the first-song confirmation and downloads the whole playlist immediately.
- `--output-dir` sets the base export directory; by default files go to `./[Album-Name]`.
- `--songs-limit` defaults to all songs when omitted.
- `--lyrics-metadata` fetches lyrics with timestamps and saves them into MP3 metadata.
- `--keep-original-audio` skips MP3 conversion/tagging and keeps the downloaded source audio extension.
- `--mp3-bitrate` controls MP3 conversion bitrate when MP3 conversion is enabled.
- `--debug` enables verbose logs for lyrics, thumbnails, metadata, and full yt-dlp output.
- `--zip-max-size` splits archives into `.partNN.zip` files once the source bytes per archive reach the limit.

If required input is missing in interactive mode, `ytm-dl` asks for it.

## tg-uploader

`tg-uploader` accepts values from CLI args first, then environment variables, then prompts.

Supported environment variables:

- `TOOLSX_TG_API_ID`, `TG_API_ID`, `TELEGRAM_API_ID`
- `TOOLSX_TG_API_HASH`, `TG_API_HASH`, `TELEGRAM_API_HASH`
- `TOOLSX_TG_BOT_TOKEN`, `TG_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`
- `TOOLSX_TG_CHAT_ID`, `TG_CHAT_ID`, `TELEGRAM_CHAT_ID`

Example:

```bash
export TOOLSX_TG_API_ID=12345
export TOOLSX_TG_API_HASH=your_api_hash
export TOOLSX_TG_BOT_TOKEN=123:token
export TOOLSX_TG_CHAT_ID=-1001234567890

tg-uploader --file ./archive.zip --caption "Nightly build"
```

Debug mode:

```bash
tg-uploader --file ./archive.zip --debug
```

## Local development

```bash
git clone https://github.com/z44d/toolsx
cd toolsx
python -m pip install -e .
```

## Adding more tools

1. Add the new module under `src/`.
2. Register it in `src/toolsx/registry.py` so `toolsx` lists it.
3. Add one console script entry under `[project.scripts]` in `pyproject.toml`.

## Release workflow

The GitHub workflow at `.github/workflows/publish.yml` builds and publishes to PyPI on version tags like `v0.1.0`.

Before using it, configure PyPI trusted publishing for the repository or provide the required PyPI credentials in GitHub.
