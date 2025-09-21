# Mantis SDK

Python SDK for interacting with the Mantis visualization platform. Provides programmatic control over space creation, data visualization, and analysis.

## Installation

```bash
python -m venv venv

# Windows
.\venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

python -m pip install -r ./src/requirements.txt

python -m playwright install chromium
```

**Note:** If the Playwright installation fails, try running:

```bash
python -m playwright install-deps
```

before proceeding.

## Authentication

The `MantisClient` object requires the parameter `cookie` to be passed in. This can be obtained by logging into the Mantis webpage in your browser, opening devtools, and finding the session cookies that are passed to the requests. The following keys in cookies are required: `session-id`, `next-auth.callback-url`, `next-auth.csrf-token`, `next-auth.session-token`. Use the cookie string as the `cookie` param.

## Quick Start

***Note***: If you are using a local backend, you must run it with docker, or the space creation will NOT work. To do so, `cd docker` from the backend root, then `docker compose up -d --build`. After you run the build once, you can re-run it simply with `docker compose up`. If things dno't work, check the logs and make sure they are not empty.

To get your cookie, go to any space (e.g. https://mantisdev.csail.mit.edu/space/d99efc56-00d5-4f18-8ecc-0620500fcf79/). Then go to the devtools, network tab. Click on the getCounter request, go to headers, and copy and paste the cookie field (should look like "session_id=...")

```python
from mantis_sdk.client import MantisClient, SpacePrivacy, DataType, ReducerModels 
from mantis_sdk.render_args import RenderArgs 
from mantis_sdk.config import ConfigurationManager
import pandas as pd
import asyncio
import json

config = ConfigurationManager()
config.update({
    "host": "https://mantisdev.csail.mit.edu",
    "domain": "mantisdev.csail.mit.edu",
    "backend_host": "https://mantiscluster.csail.mit.edu"
})

mantis = MantisClient("/api/proxy/", cookie, config=config)

# Create DF (Real data will need more points)
df = pd.DataFrame({
    "Symbol": ["AAPL", "GOOGL", "MSFT", "AMZN", "FB"] + ["COMPANY"+str(i) for i in range(100)],
    "Market Cap": [2000, 1500, 1300, 1200, 800] + [i*10 for i in range(100)],
    "Description": ["Apple Inc.", "Alphabet Inc.", "Microsoft Corporation", "Amazon.com Inc.", "Facebook Inc."] + ["This is company number "+str(i) for i in range(100)]
})

# Set types
data_types = {"Symbol": DataType.Title,
              "Market Cap": DataType.Numeric,
              "Description": DataType.Semantic}

# Make space
new_space_id = mantis.create_space("Stock data", 
                                   data=df, 
                                   data_types=data_types,
                                   reducer=ReducerModels.UMAP,
                                   privacy_level=SpacePrivacy.PRIVATE)["space_id"]

print("Created space:", new_space_id)

annotations = asyncio.run(mantis.get_annotations(new_space_id))

print("=" * 20, "Annotations", "=" * 20)
print(json.dumps(annotations, indent=2))```

## Key Features

Create spaces from DataFrames or CSV files
Control visualization layout and rendering
Execute code within spaces
Generate plots and capture screenshots
Manage panels and space configuration
Support for async operations
```

## Data Types

```python 
DataType.Title # Title/ID field 
DataType.Semantic # Text for semantic analysis
DataType.Numeric # Numerical values 
DataType.Categoric # Categorical values 
DataType.Date # Date/time values 
DataType.Links # URL/link fields 
```

## Space Privacy Levels

```python 
SpacePrivacy.PUBLIC # Visible to all users 
SpacePrivacy.PRIVATE # Only visible to owner 
SpacePrivacy.SHARED # Visible to specific users 
```

## Dimension Reduction

```python 
ReducerModels.UMAP # UMAP reduction 
ReducerModels.PCA # PCA + UMAP 
ReducerModels.TSNE # t-SNE 
```

## AI Providers

```python 
AIProvider.OpenAI
AIProvider.HuggingFace
```
