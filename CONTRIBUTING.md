# Contributing to ModelWatcher

Thank you for your interest in contributing. This guide covers the essentials.

## Getting started

1. **Fork and clone** the repository.
2. **Install dependencies**: `pip install -r requirements.txt && npm install`
3. **Copy config templates**: `cp config/app.yaml.example config/app.yaml && cp config/models.yaml.example config/models.yaml`
4. **Set required env vars**: See `.env.example` for the full list.
5. **Run the server**: `python -m uvicorn backend.main:app --reload --reload-dir backend`

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for a full local setup guide.

## Branch strategy

| Branch | Purpose |
|--------|---------|
| `testing` | Active development. All PRs target this branch. |
| `main` | Stable releases. Merged from `testing` when ready. |

- Create feature branches from `testing`
- Open pull requests against `testing`, not `main`
- `main` is updated via merge from `testing` after review

## Code style

### Python (backend)

- Follow PEP 8
- No hardcoded defaults for config values - all tuning comes from `config/app.yaml` via `st.c.*` access
- Error responses must use `{"error": "message"}` format (enforced by exception handlers in `main.py`)
- All `except` blocks must call `log_error()` or re-raise - never silently swallow
- Shared state primitives use `import backend.state as st` and `st.variable = value` (never `from backend.state import x` then rebind)
- Named exports only in ES modules - no default exports

### JavaScript (frontend)

- ES modules, no bundler
- Named exports only (`export function foo()`, not `export default`)
- No dynamic class names for Tailwind (e.g., `` `text-${color}` ``) - use explicit class maps so Tailwind's scanner finds them
- All `catch` blocks must call `logError()` or re-throw - never empty `catch {}`
- Every `.then()` chain must have a `.catch()`

### CSS

- Tailwind CSS v4, configured in `frontend/input.css` (no `tailwind.config.js`)
- Use the custom color palette (`accent`, `success`, `warn`, `danger`, `teal`, `surface`) not Tailwind's default colors
- After changing any CSS class names in HTML or JS, run `npm run build:css` to rebuild

### Comments and documentation

- Explain **why**, not **what** - the code already shows what it does
- Use sentence case for all comments and markdown headers
- No em dashes (U+2014/U+2013) - use regular hyphens
- Keep comments minimal - only non-obvious decisions and gotchas

## Testing

- Run the test suite: `npm test` (or `python3 -m pytest scripts/tests/ -v`)
- Add tests for bug fixes that reproduce the issue
- Tests are in `scripts/tests/`
- Do not run performance test scripts (`npm run perf`, `npm run stress`) - these are for the deployment environment only

## Commit messages

- Use imperative mood: "Add X", "Fix Y", "Remove Z"
- Reference issues when applicable: "Fix #123"
- Keep the first line under 72 characters

## Pull requests

- Fill out the pull request template completely
- Link related issues
- Keep PRs focused - one feature or fix per PR
- Ensure all tests pass before requesting review

## Reporting bugs

Use the bug report template. Include:

- Steps to reproduce
- Expected vs actual behavior
- Browser and OS information
- Relevant console errors or server logs

## Feature requests

Use the feature request template. Describe:

- The problem you are trying to solve
- Your proposed solution
- Alternative approaches you considered

## Security vulnerabilities

See [SECURITY.md](SECURITY.md) - do not open public issues for security vulnerabilities.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
