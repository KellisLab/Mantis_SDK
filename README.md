# Mantis SDK

Pythonic SDK for the [Mantis](https://github.com/KellisLab) visualization platform. It talks to the
Mantis backend over REST and drives the Mantis frontend through Playwright browser automation.

## Installation

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
- **Internal-service (backend-to-backend).** Set `config.internal_user_id` (or `MANTIS_INTERNAL_USER_ID`);
  the SDK then sends `X-Internal-Service: true` + `X-Internal-User-Id` instead of a cookie.

`MantisClient.from_env()` reads `MANTIS_HOST`, `MANTIS_BACKEND_HOST`, `MANTIS_COOKIE`,
`MANTIS_BASE_URL`, and `MANTIS_INTERNAL_USER_ID`.

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

> Local backend must run via Docker for space creation to work (`cd docker && docker compose up -d --build`).

## Resource-oriented API

| Group | Highlights |
|-------|-----------|
| `client.spaces` | `create(...)`, `from_github(repo_url)`, `get_all()`, `aopen(space_id)` |
| `client.maps` | `list(space_id)`, `list_idea_ids(map_id)`, `get_ideas(ids)` |
| `client.notebooks` | `resolve_map_to_project`, `create`, `from_map` |
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

See [`examples/`](./examples) for runnable scripts.
