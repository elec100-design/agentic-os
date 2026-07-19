# Contributing to Agentic OS

Thanks for your interest in contributing! Agentic OS is a local FastAPI + HTMX
dashboard that orchestrates subscription AI CLIs — no build tools, no bundler,
just Python + Jinja2 + vanilla JS.

## Getting started

```bash
git clone https://github.com/elec100-design/agentic-os.git
cd agentic-os
uv venv .venv
uv pip install -r requirements.txt -r requirements-dev.txt --python .venv/bin/python3
```

Run the dev server:

```bash
AOS_DISABLE_WORKER=1 .venv/bin/uvicorn app.main:app --port 8899
```

`AOS_DISABLE_WORKER=1` skips launching the background job worker and refresh
loops — handy for UI-only iteration. Drop it to test the full pipeline
(requires at least one AI CLI installed and authenticated, see [README](README.md)).

## Running tests

```bash
.venv/bin/pytest -q
```

Tests use a `tmp_env` fixture (`tests/conftest.py`) that monkeypatches all
`app.config` paths to a temp directory, so nothing touches your real `data/`.
Async tests use `pytest-asyncio` (`asyncio_mode = "auto"`, no decorator needed).

## Project layout

See the [Architecture section](README.md#architecture) in the README for a
directory map. A few conventions worth knowing before you dig in:

- **No build step.** `static/app.js` and `static/style.css` are hand-written,
  vendored (no CDN, no bundler). Keep it that way — see
  [`docs/plan.md`](docs/plan.md) for why.
- **Providers are adapters.** Each AI CLI implements `build_command` /
  `parse_output` / `detect_rate_limit` (see `app/providers.py`). Adding a new
  CLI means adding a new provider class + registering it in `PROVIDERS`, plus
  entries in `app/setup.py`'s `CLI_META` so the setup wizard can detect it.
- **Settings persist as JSON, not `aos.env`.** `aos.env` is read-only at
  runtime (loaded once at import). User-toggleable state (like enabled
  agents) lives in `data/*.json` — see `app/settings.py` and `app/workspace.py`
  for the pattern.
- **Everything defensive on corrupt/missing state.** Config loaders never
  crash a request on a missing or malformed JSON file — they fall back to
  safe defaults (see `app/settings.py:load()`).

## Before opening a PR

1. Run the full test suite — `pytest -q` should be green.
2. Keep changes scoped. If you're touching UI, describe what you tested
   manually (this app has no browser test suite yet) — a screenshot or GIF
   helps a lot.
3. Match the existing comment style: comments explain *why*, not *what*.
   Korean comments in existing code are fine to keep; new comments in either
   language are welcome.
4. If you're adding a new environment variable, document it in both
   `aos.env.example` and the README config table.

## Reporting bugs

Please include your OS, the CLI(s) you're using (and their versions), and —
if the dashboard loaded at all — what `/api/setup/status` returns
(`curl http://localhost:8899/api/setup/status`). This is usually enough to
tell whether a CLI wasn't detected vs. a real bug.

## Questions / ideas

Open an issue. Roadmap and design rationale live in
[`docs/plan.md`](docs/plan.md); completed work history is in
[`docs/task.md`](docs/task.md).
