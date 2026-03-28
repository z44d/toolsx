## AGENTS.md

This repository is a small Python 3.11 CLI toolbox.
The main entry points are `toolsx`, `ytm-dl`, `tg-uploader`, and `subtitle-extract`.
Agents working here should optimize for safe CLI changes, minimal surprise, and clean terminal UX.

## Repository Facts

- Package/build metadata lives in `pyproject.toml`; source code lives under `src/`.
- Runtime dependencies are declared in `pyproject.toml` and duplicated in `requirements.txt`.
- There is no committed `tests/` directory or repo-local lint/format config right now.
- User-facing CLI docs live in `README.md`; release automation lives in `.github/workflows/publish.yml`.
- Main code entry points are `src/toolsx/cli.py`, `src/ytm_dl.py`, `src/tg_uploader.py`, `src/subtitle_extractor.py`, and `src/toolsx/registry.py`.
- No Cursor rules were found in `.cursor/rules/` or `.cursorrules`.
- No Copilot instructions were found in `.github/copilot-instructions.md`.

## Environment And Setup

- Target Python is `>=3.11`.
- Do not assume `python` exists on PATH; in this repo `python3` worked and `python` did not.
- Prefer `python3` for generic commands.
- If using the checked-in virtualenv, `./venv/bin/python` and `./venv/bin/<tool>` are available.
- For editable local development, use `python3 -m pip install -e .`.
- For isolated local verification, using `./venv/bin/python` is the safest option in this repository.

## Build Commands

- Install package editable: `python3 -m pip install -e .`
- Install build tooling: `python3 -m pip install --upgrade build twine`
- Build sdist and wheel: `python3 -m build`
- Build with repo virtualenv: `./venv/bin/python -m build`
- Check built artifacts: `python3 -m twine check dist/*`

## Lint / Static Checks

There is no configured Ruff, Black, Flake8, mypy, or pytest setup in the repo today.
Use lightweight Python-native checks unless you are also introducing a tool config.

- Syntax check all source: `python3 -m compileall src`
- Syntax check with repo virtualenv: `./venv/bin/python -m compileall src`
- File-level syntax check: `python3 -m py_compile src/tg_uploader.py`

## Test Commands

There is no committed automated test suite right now; `python3 -m unittest discover` currently reports `Ran 0 tests`.

- Discover stdlib tests: `python3 -m unittest discover`
- Run a single unittest file if tests are added: `python3 -m unittest tests.test_module`
- Run a single unittest case: `python3 -m unittest tests.test_module.TestCaseName`
- Run a single unittest method: `python3 -m unittest tests.test_module.TestCaseName.test_method`

If you add pytest in the future, document it in this file and `pyproject.toml`.
Do not claim pytest support unless the repo actually gains pytest as a dependency/configured tool.

## Practical Smoke Tests

Because the project is CLI-first, smoke tests matter more than unit tests right now.

- Show tool registry: `./venv/bin/python -m toolsx`
- Show downloader help: `./venv/bin/python src/ytm_dl.py --help`
- Show uploader help: `./venv/bin/python src/tg_uploader.py --help`
- Show subtitle extractor help: `./venv/bin/python src/subtitle_extractor.py --help`

Be careful with end-to-end runs:

- `ytm-dl` downloads network content and writes files.
- `tg-uploader` uploads real files to Telegram.
- `subtitle-extract` may access network subtitle endpoints and write local files.
- Prefer `--help`, argument parsing checks, and isolated helper tests unless the task explicitly requires live network behavior.

## Release Workflow

- Publishing is handled by `.github/workflows/publish.yml`.
- The workflow runs on git tags matching `v*`.
- It validates that the tag equals `v{project.version}` from `pyproject.toml`.
- It installs `build` and `twine`, runs `python -m build`, checks `dist/*`, then publishes.
- If you change versioning or packaging behavior, update both `pyproject.toml` and the workflow expectations.

## Coding Style Overview

Follow the existing source style over generic style advice.
This codebase is handwritten, type-annotated, procedural, and CLI-focused.

## Imports

- Start files with `from __future__ import annotations`.
- Group imports as: standard library, third-party, local modules.
- Separate import groups with a single blank line.
- Prefer direct module/function imports that match existing patterns.
- Multiline imports use parentheses when needed, as in Rich imports.
- Remove unused imports when touching a file.

## Formatting

- Use 4-space indentation.
- Keep line lengths readable; the repo has some long lines, but do not create avoidable horizontal sprawl.
- Prefer readable multi-line argument lists over dense one-line calls.
- Preserve ASCII unless a file already needs Unicode content.
- Add comments sparingly; most functions in this repo are intentionally self-explanatory.

## Types

- Add type annotations to public helpers and new internal helpers.
- Existing code mixes `list[Path]` with `List[Path]`; prefer consistency within the file you edit.
- Use dataclasses for lightweight structured data (`ToolSpec`, `TrackInfo`, `UploadConfig`).
- Prefer `Path` over raw string paths once values have been parsed.
- Use `cast(...)` only when narrowing external library return types that are otherwise too loose.

## Naming Conventions

- Functions and variables use `snake_case`.
- Classes and dataclasses use `PascalCase`.
- Constants use `UPPER_SNAKE_CASE`.
- CLI flags use kebab-case, e.g. `--output-dir`, `--keep-original-audio`.

## Control Flow And Structure

- Keep parsing, validation, network work, and presentation reasonably separated.
- Centralize reusable terminal output in helpers like `print_banner()`, `debug_log()`, and summary printers.
- Return integer exit codes from `main()`/`sync_main()`.
- Only use `raise SystemExit(main())` at the module entry guard.

## Error Handling

- Raise specific exceptions in helpers when possible: `ValueError`, `FileNotFoundError`, `RuntimeError`.
- Catch exceptions near the CLI boundary and convert them into friendly Rich output plus exit codes.
- Do not leak raw tracebacks for normal user errors.
- Preserve underlying exceptions with `raise ... from exc` when wrapping lower-level failures.
- For external services and downloads, prefer graceful degradation when possible (for example, skipping artwork or lyrics rather than aborting everything).

## CLI UX Expectations

- Use Rich for interactive output, tables, progress bars, and panels.
- Interactive prompts should have sensible defaults.
- Non-interactive mode should fail clearly if required inputs are missing.
- Debug logging should be gated behind a boolean flag and should not pollute normal output.

## Filesystem And Side Effects

- Use `pathlib.Path` for path handling.
- Be careful with user data and large generated artifacts.
- Do not commit or depend on ignored secrets/files such as `browser.json` or `*.cookies.txt`.
- Treat `.download-archive.txt`, exported media, `build/`, and `dist/` as generated outputs, not hand-edited source.

## Dependencies And External Tools

- `bun` is optional; the code already handles its absence.
- `ytmusicapi`, `yt-dlp`, `requests`, `rich`, and Telegram-related libraries are core runtime dependencies.
- When introducing a new dependency, add it to `pyproject.toml` and update `requirements.txt` if the repo continues to keep both in sync.

## When Changing Behavior

- Update `README.md` for user-visible CLI changes.
- Update `pyproject.toml` if entry points, dependencies, or versioning change.
- Update `src/toolsx/registry.py` when adding a new tool.

## Guidance For Agents

- Preserve the existing CLI tone and Rich-based presentation style.
- Do not invent nonexistent lint/test infrastructure in commits or documentation.
- If you add tests or tooling, document the exact commands here.
- Before finishing, run at least a syntax check and the most relevant CLI smoke test for the files you changed.
