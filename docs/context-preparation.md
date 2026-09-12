# Prepare frozen context explicitly

`AgentSession.prepare_context()` freezes a chosen set of sources through the
server's snapshot API. It returns the snapshot's version, ID, digest, and source
summary. The server checks source access and captures the selected records and
file versions.

```mermaid
flowchart LR
    Sources[Spaces, selected records, files] --> Prepare[prepare_context]
    Prepare --> Authorize[Server checks source access]
    Authorize --> Freeze[Server captures source versions]
    Freeze --> Snapshot[Snapshot ID, digest, and source summary]
```

This gives a scientific analysis an explicit source reference: later changes to
the live selection do not redefine what the server captured. Source counts and
the digest provide a record of the prepared context. Preparation does not open a
WebSocket or send an analysis turn; existing `ask()` calls keep their current
behavior. A delivery client must explicitly use the returned snapshot to run
against it.

```python
import uuid

session = client.agents.session(
    space_id,
    user_email="researcher@example.com",
    chat_id=str(uuid.uuid4()),
    space_state_id=space_state_id,
)
snapshot = session.prepare_context(
    active_map_id=map_id,
    point_ids=selected_point_ids,
)
print(snapshot["id"], snapshot["digest"])
```

The new preparation method requires a stable UUID chat identity. An existing
session can also use the server chat ID captured during an earlier run. These
requirements apply only to preparation; existing session constructors and
defaults are unchanged.

| Source choice | Meaning |
| --- | --- |
| No membership filters and omitted `space_ids` | Capture the scoped Space's records |
| Nonempty `bag_ids`, `cluster_ids`, or `point_ids` | Capture those members; require `active_map_id` and a bound Space thread |
| Explicit `space_ids=[]` | Omit whole-Space sources; keep any explicitly selected members or attachments |
| Explicit `space_ids=[...]` | Include those Spaces, subject to server authorization |
| `files=[{"space_id": ..., "path": ...}]` | Attach server-managed files |
| `source_snapshot_ids=[...]` | Include sources from accessible existing snapshots |
| `open_notebook_path=...` | Preserve notebook context in the snapshot manifest |

Returned snapshot version and UUID are validated before the method returns.
The method preserves server errors and does not substitute other sources.
