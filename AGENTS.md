# AGENTS.md

## Tooling: use `uv` for all Python work

Never run `python`, `pip`, or `pytest` directly. The system `python3` is 3.9, which cannot even
import this codebase (`int | None` in pydantic fields, `zoneinfo` semantics, CI targets 3.12/3.14).

Set up once per clone, from `backend/`:

```bash
uv venv --python 3.12 .venv
uv pip install -r requirements-dev.txt
```

Then prefix every Python command with `uv run`:

```bash
uv run python -m pytest -q tests/
uv run python -m pyflakes .
```

`.venv/` is already gitignored. No `pyproject.toml` exists, so no `--no-project` flag is needed.

Frontend is unaffected — `npm run -s test` / `npm run -s build` from `frontend/`.