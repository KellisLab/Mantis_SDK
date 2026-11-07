# SDK usage reference

[← Back to the SDK overview](../README.md)

Pythonic SDK for the [Mantis](https://github.com/KellisLab) visualization platform. It talks to the
Mantis backend over REST and drives the Mantis frontend through Playwright browser automation.

## Installation

Start with the [source installation instructions](../README.md#installation).

```bash
python -m venv venv && source venv/bin/activate   # Windows: .\venv\Scripts\activate

pip install -e .                 # core (REST only)
pip install -e ".[browser]"      # + Playwright browser automation
pip install -e ".[dev]"          # + test/lint/type tooling

# only if you installed the browser extra:
python -m playwright install chromium
```

## Authentication

Two ways to authenticate:

- **Session cookie (user context).** Log into Mantis, open devtools → Network, copy the `cookie`
  header from any authenticated request, and pass it as `cookie`. Required keys include
  `next-auth.session-token` and `sessionid`.
- **Internal-service (backend-to-backend).** Set `config.internal_user_id` and
  `config.internal_service_secret` (or `MANTIS_INTERNAL_USER_ID` and `MANTIS_INTERNAL_SERVICE_SECRET`);
  the SDK then sends authenticated internal-service headers. A cookie is not required.

If both methods are configured, the SDK sends both sets of headers and the backend uses the
authenticated internal-service identity. If a cookie is present but the internal user ID has no
service secret, the SDK sends only the cookie; without a cookie, incomplete internal configuration
fails before making a request.

`MantisClient.from_env()` reads `MANTIS_HOST`, `MANTIS_BACKEND_HOST`, `MANTIS_COOKIE`,
`MANTIS_BASE_URL`, `MANTIS_INTERNAL_USER_ID`, and `MANTIS_INTERNAL_SERVICE_SECRET`.

## Quick start

```python
import pandas as pd
from mantis_sdk import MantisClient, ConfigurationManager, DataType, ReducerModels, SpacePrivacy

config = ConfigurationManager().update({
    "host": "https://mantisdev.csail.mit.edu",
    "domain": "mantisdev.csail.mit.edu",
    "backend_host": "https://mantiscluster.csail.mit.edu",
})

client = MantisClient("/api/proxy/", cookie=COOKIE, config=config)

df = pd.DataFrame({
    "Symbol": ["AAPL", "GOOGL", "MSFT"] + [f"CO{i}" for i in range(100)],
    "Market Cap": [2000, 1500, 1300] + [i * 10 for i in range(100)],
    "Description": ["Apple", "Alphabet", "Microsoft"] + [f"Company {i}" for i in range(100)],
})

space = client.spaces.create(
    "Stock data", df,
    {"Symbol": DataType.Title, "Market Cap": DataType.Numeric, "Description": DataType.Semantic},
    reducer=ReducerModels.UMAP, privacy_level=SpacePrivacy.PRIVATE,
    show_progress=True,
)
print(space.space_id, space.map_id)
print(space.get_annotations())
```

> Space creation needs a reachable MantisAPI backend and its processing services. See the [backend setup instructions](https://github.com/KellisLab/MantisAPI#readme) for local development.

## Resource-oriented API

| Group | Highlights |
|-------|-----------|
| `client.spaces` | `create(...)`, `from_github(repo_url)`, `get_all()`, `aopen(space_id)` |
| `client.maps` | `list(space_id)`, `list_idea_ids(map_id)`, `get_ideas(ids)` |
| `client.notebooks` | `resolve_map_to_project`, `create`, `from_map` |
| `client.agents` | `session(...)`, `run_sync(...)`, `providers(email)`, `set_provider(email, provider)` |
| `client.annotations` | `list(map_id)`, `create(map_id, payload)` |
| `client.search` | `cluster_questions(...)` *(backend route currently disabled)* |

Legacy flat methods (`create_space`, `get_spaces`, `open_space`, `get_annotations`,
`getClusterQuestions`) still work but emit `DeprecationWarning`.

`create_space` now returns a `SpaceHandle` (a dict subclass, so `["space_id"]` still works) with
`.list_maps()`, `.get_annotations()`, `.points` (lazy paginated iterator), `.aopen()`, `.delete()`.

## Notebooks

```python
project_id = client.notebooks.resolve_map_to_project(map_id)
nb = client.notebooks.create(project_id, name="Analysis", user_id=USER_ID)

cell = nb.add_cell("import numpy as np; print(np.arange(5))")
cell.execute()
print(cell.text)

plot = nb.add_cell("import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.show()")
plot.execute()
open("plot.png", "wb").write(plot.image_png_bytes())

nb.checkpoint("after-plot")          # snapshot kernel state
nb.list_checkpoints()
```

## Agents (provider-scoped)

Run a coding agent scoped to a space and stream its output. `Provider.OpenCode` is the default.
Other providers depend on the account capabilities returned by `client.agents.providers(email)`.
For example, `Provider.ClaudeCode` requires `bedrock_enabled`; the SDK raises
`ProviderUnavailableError` when that capability is missing.

The agent runtime identifies the user by **email** (set `config.user_email` or pass `user_email=`),
not user_id.

```python
from mantis_sdk import Provider

async with client.agents.session(space_id, provider=Provider.OpenCode, user_email=email) as run:
    async for ev in run.ask("Summarize the outlier cluster", active_map_id=map_id):
        if ev.type == "text":      print(ev.text, end="")
        elif ev.type == "tool_use": print("→", ev.tool_name)
    result = run.result()          # full text + events; result.provider asserts the runtime used

# sync convenience
result = client.agents.run_sync("What patterns are here?", space_id, provider=Provider.OpenCode,
                                user_email=email)

# capability management
client.agents.providers(email)                     # {providers, default, current}
client.agents.set_provider(email, Provider.ClaudeCode)
```

Event types: `text`, `tool_use`, `tool_result`, `thinking`, `init`, `typing`, `complete`, `fail`.

## Browser automation

```python
async with await client.spaces.aopen(space_id) as space:
    dims = await space.get_available_dimensions()
    hits = await space.general_search("inflation", search_type="semantic", limit=10)
    await space.add_bag("my bag", [p["id"] for p in hits])
    png = await space.render_plot("x", "y")
    await space.command("any_executor_name", arg1, arg2)   # generic escape hatch
```

## Data types

`DataType.Title`, `Semantic`, `Numeric`, `Categoric`, `Date`, `Links`, `CustomModel`, `Image`,
`Geospatial`, `Coordinate1`, `Coordinate2`, `Connection`, `Vector`, `Delete`. Values mirror the
backend serializer keys exactly (e.g. `CustomModel == "custom_model"`).

## Errors

All errors derive from `MantisError`: `AuthenticationError`, `NotFoundError`, `RateLimitError`,
`APIStatusError(status_code, body)`, `APIConnectionError`, `SpaceCreationError`,
`FeatureUnavailableError`, `ExecutionError`.

See [`examples/`](../examples) for runnable scripts.

For selections, shared tables, artifacts and refresh, return to the
[shared-data walkthrough](../README.md#walkthrough-selected-points-to-a-reusable-result).


## Existing semantic objects


The Semantic Object Runtime carries original URI/version/hash pins between
Knowledge, Composer, notebook workflows and the SDK. These endpoints currently
require a **Django session cookie containing `sessionid` and `csrftoken`**;
internal-service credentials alone do not authorize them. The SDK sends the
matching CSRF header. Keep session cookies in environment/configuration, never
in shared notebooks.

```python
obj = client.objects.resolve(space_id, map_id, object_uri)
object_input = client.objects.workflow_input(
    space_id, map_id, [obj], message="Analyze this exact original"
)
run = client.objects.run_workflow(workflow_definition, object_input)
status = client.objects.execution(run["execution_id"])
```

`object_input` is also accepted by `executeWorkflow(workflow_id, object_input)`
in a Mantis notebook. Construct it with `client.objects.workflow_input` without
copying source content. The backend resolves originals at admission and
again when the queued worker starts. Changed versions or revoked access require
resolving and reviewing again; the SDK never silently substitutes newer content.
Completed result reads retain the originating executor and source-access checks.

This path supports existing passage, claim and workflow-result-row objects. For selections,
DataFrames and Rep-SI matrices, see the [shared-data walkthrough](../README.md#walkthrough-selected-points-to-a-reusable-result).

