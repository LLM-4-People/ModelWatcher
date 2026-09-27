# Redesign findings register

Single source of truth for every issue found while working on the UI redesign, including low-impact ones.
Each entry records how it was reproduced, its root cause, and what was done about it.

Status values: `open`, `investigating`, `fixed`, `deferred-to-redesign` (resolved by the redesign itself, tracked in its plan).

| ID | Area | Summary | Severity | Status |
|----|------|---------|----------|--------|
| F1 | scripts | `scale_test_db.py` resolves the project root to `scripts/`, so it cannot open `data/metrics-scale-test.db` | medium | open |
| F2 | backend | `streaming.py` downloads the tiktoken encoding at import time, so the server cannot start without outbound access to `openaipublic.blob.core.windows.net` | high | open |
| F3 | backend/docs | Built CSS path defaults to `/opt/frontend/tailwind.min.css`, so following DEVELOPMENT.md locally serves a 404 for the stylesheet and an unstyled page; the documented build output is not gitignored | medium | open |
| F4 | frontend | Inter font is preloaded from `cdn.jsdelivr.net`, an external dependency for an app whose PWA shell is meant to work offline | low | deferred-to-redesign |
| F5 | frontend | Header shows a red connection dot and a "Server unreachable - retrying automatically" banner against a healthy local server | medium | investigating |
| F6 | frontend | Model modal header renders `Bench 5h 2m ·OK10h 1m` with a missing space and inconsistent separator | low | investigating |
| F7 | docs | DEVELOPMENT.md installs Python dependencies into the system interpreter; on Debian-patched Python `http-ece` fails to build (`install_layout`), a virtualenv avoids it | low | open |

## Details

### F1 - seeder project root

- Reproduce: `python3 scripts/util/scale_test_db.py --providers 5 --models-per 4 --months 0.5` fails with `sqlite3.OperationalError: unable to open database file`.
- Root cause: `Path(__file__).resolve().parent.parent` was correct before the script moved into `scripts/util/`; it now points at `scripts/`. The docstring usage line still references the old location.

### F2 - import-time network fetch

- Reproduce: start the server in an environment without access to `openaipublic.blob.core.windows.net`; startup fails with `requests.exceptions.ProxyError` from `tiktoken.get_encoding("o200k_base")`, even with `MW_DISABLE_TESTS=1`.
- Root cause: `backend/streaming.py` builds the encoder at module import. The Docker image does not pre-cache the encoding, so every fresh container depends on that host at startup.

### F3 - built CSS path

- Reproduce: follow DEVELOPMENT.md (`npm run build:css`, then run uvicorn). `GET /frontend/tailwind.min.css` returns 404 and the dashboard renders unstyled. `git status` then shows `frontend/tailwind.min.css` as untracked.
- Root cause: `backend/state.py` defaults `MW_BUILT_CSS_PATH` to the Docker location. The Docker location exists because compose mounts the repo read-only over `/app`, which would hide a CSS file built into `/app/frontend`.

### F4 - external font dependency

- Observed: the sandbox proxy rejected `cdn.jsdelivr.net`; the page fell back to system fonts.
- Plan: the redesign self-hosts its fonts alongside the vendored Chart.js.

### F5 - false "server unreachable" state

- Observed in Playwright screenshots of a healthy local instance started with `MW_DISABLE_TESTS=1`.
- Next: reproduce and root-cause (WebSocket handshake, ping, or status logic tied to disabled tests).

### F6 - modal header separator

- Observed in the modal screenshot for `AlphaAI::gpt-5.4`.
- Next: find the formatter that builds the header status line.

### F7 - dependency install instructions

- Reproduce: `pip install -r requirements-dev.txt` on Debian's Python 3.11 fails building `http-ece`. Installing into a virtualenv succeeds.
