# Redesign worklog

Running log of the UI redesign: goals, progress per round, how we build and test, and what we changed about the process.
Issues live in [FINDINGS.md](FINDINGS.md).

## Goals

- Make the dashboard modern, pleasing to look at and to use.
- It should feel smart and adaptive, be convenient, and be easy to understand for newcomers and operators alike.
- Show novel ideas first; implement only after a direction is chosen.

## Engineering rules for this work

- DRY, KISS, YAGNI; build shared modules, functions and tests meant for reuse.
- One source of truth for every value; no static tunables in code (config lives in `config/app.yaml`, surfaced through `st.c.*` and `/api/config`).
- Fix at the lowest choke point; no band-aids, no backward-compatibility shims.
- Expose settings, signals and actions for everything built.
- All logging goes through the central loggers (`backend/state.py` `log`/`log_error`, `frontend/js/utils.js` `logDebug`/`logInfo`/`logWarn`/`logError`).
- Every finding gets a test that would catch it next time.
- One writing agent at a time; any number of reading agents.

## How we build and test

- Local instance: virtualenv with `requirements-dev.txt`, seeded with `scripts/util/scale_test_db.py`, run with `MW_DISABLE_TESTS=1` against the scale-test config.
- Visual checks: Playwright with the pre-installed Chromium, screenshots at 1440, 1280, 820 and 390 px in light and dark themes.
- Tests: `python3 -m pytest scripts/tests/ -v`.

## Rounds

### Round 1 - discovery and ideation

- Ran the app locally with seeded data and captured screenshots of the current UI.
- Workflow `redesign-understand-ideate`: four readers (UI inventory, data and API, UX critique, constraints) feed five concept generators (decision-first, calm and ambient, power-user flow, storytelling and time, visual language), two concepts each.
- Next: a judge panel scores the concepts, synthesis into three or four directions, then interactive mockups for each direction on a shared sample dataset.

### Process notes

- Environment: general web egress is blocked (Kagi, jsdelivr, the live instance), so research agents use the built-in web search and fetch tools.
- `pkill -f` with a pattern that also appears in the invoking shell command kills the shell itself; use a bracketed pattern such as `pgrep -f "[u]vicorn backend"`.
