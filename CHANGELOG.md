# Changelog

This project follows [Semantic Versioning](https://semver.org/). Breaking changes bump the minor
version while pre-1.0.

## [0.12.0] — 2026-06-14

### Added
- **Agent runtime, provider-scoped** (`client.agents`). Run `opencode` (default, universal) or
  `claude_code` (opt-in, capability-gated) agents scoped to a space, streaming normalized events.
  - `client.agents.session(space_id, provider=..., user_email=...)` → async streaming
    `AgentSession`; `run.ask(...)` yields `AgentEvent`s (`text`/`tool_use`/`tool_result`/`thinking`/
    `complete`/`fail`); `run.result()` assembles an `AgentResult`.
  - `client.agents.run_sync(...)` sync convenience.
  - `client.agents.providers(email)` / `set_provider(email, provider)` over
    `/agent_execution/providers/`.
  - New `Provider` enum (`OpenCode`, `ClaudeCode`); `Provider.OpenCode` is the default.
  - `ProviderUnavailableError` raised up front when `claude_code` isn't `bedrock_enabled`
    (no silent fallback), and on detecting the run reported a different provider than requested.
  - `AgentRunError` for mid-stream failures / timeouts.
  - `ConfigurationManager.user_email` (or `MANTIS_USER_EMAIL`) — the agent runtime keys identity
    + capability gating on email, not user_id.
  - Contract verified live against `ws/chat/<chat_id>/<email>/default/?model_id=<provider>`.

## [0.11.0] — 2026-06-11

### Fixed (compatibility with current MantisAPI)
- `create_space` no longer calls the removed `synthesis/parameters` / `select-umap` endpoints; it
  polls `synthesis/progress/<map_id>/` and finishes on `completed`/`progress>=100` or raises on `error`.
- Progress is keyed on `map_id` (was `space_id`); the create response is read as `{map_id, space_id}`
  (the old `layer_id` field is gone).
- `DataType.CustomModel` is now `"custom_model"` (was `"customModel"`, which DRF silently dropped).
  Added `Image`, `Geospatial`, `Vector` to match the backend serializer.
- `get_annotations` uses REST `GET /api/getAnnotations/` (the `ws/space/` socket was removed).
- `getClusterQuestions` raises `FeatureUnavailableError` (route disabled backend-side) instead of a raw 404.
- `custom_models` length is validated against the column count, not the `data_types` subset.

### Added
- Resource-oriented API: `client.spaces` / `maps` / `notebooks` / `annotations` / `search`.
- Notebook subsystem: `resolve_map_to_project`, `create`, `Notebook.add_cell`, `Cell.execute`,
  `Cell.text`, `Cell.image_png_bytes`, plus checkpoints and dill export.
- `client.spaces.from_github(repo_url)`; scaffolds for molecules / h5ad / embed-only.
- Expanded `Space` browser commands (dimensions, cluster tree, bags, search, point details, map
  transforms) and a generic `Space.command(name, *args)` escape hatch; `Space` is now an async CM.
- Typed `StrEnum` enums + `py.typed`; full exception hierarchy under `MantisError`.
- `requests.Session` transport with retry/backoff, per-call timeouts, and cookie-redacted debug logging.
- `MantisClient.from_env()`; internal-service auth via `internal_user_id`.

### Changed
- Packaging moved to `pyproject.toml` (PEP 621) with `[browser]` / `[progress]` / `[dev]` extras;
  `setup.py` removed.
- Legacy flat methods retained as deprecating shims.

### Security
- Removed a real session cookie that had been committed in `main.py`; examples use placeholders.
