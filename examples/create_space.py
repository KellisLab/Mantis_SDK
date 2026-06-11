"""create a space from a DataFrame and poll it to completion.

auth: paste a browser session cookie into MANTIS_COOKIE, or set MANTIS_INTERNAL_USER_ID
for backend-to-backend auth. configure hosts via MANTIS_HOST / MANTIS_BACKEND_HOST."""
import os

import pandas as pd

from mantis_sdk import ConfigurationManager, DataType, MantisClient, ReducerModels, SpacePrivacy

config = ConfigurationManager().update({
    "host": os.getenv("MANTIS_HOST", "http://localhost:3000"),
    "backend_host": os.getenv("MANTIS_BACKEND_HOST", "http://localhost:8000"),
    "domain": os.getenv("MANTIS_DOMAIN", "localhost"),
})

# cookie auth (recommended for user contexts); never commit a real cookie.
client = MantisClient("/api/proxy/", cookie=os.environ["MANTIS_COOKIE"], config=config)

df = pd.DataFrame({
    "Symbol": ["AAPL", "GOOGL", "MSFT"] + [f"CO{i}" for i in range(100)],
    "Market Cap": [2000, 1500, 1300] + [i * 10 for i in range(100)],
    "Description": ["Apple", "Alphabet", "Microsoft"] + [f"Company {i}" for i in range(100)],
})

data_types = {
    "Symbol": DataType.Title,
    "Market Cap": DataType.Numeric,
    "Description": DataType.Semantic,
}

space = client.spaces.create(
    "Stock data",
    df,
    data_types,
    reducer=ReducerModels.UMAP,
    privacy_level=SpacePrivacy.PRIVATE,
    show_progress=True,
    on_progress=lambda pct, msg, tree: print(f"{pct:3d}% {msg or ''}"),
)

print("created:", space)
print("annotations:", space.get_annotations())
