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

```python
from client import MantisClient, SpacePrivacy, DataType, ReducerModels 
from render_args import RenderArgs 
import pandas as pd

mantis = MantisClient("/api/proxy/", cookie)

# Create DF (Real data will need more points)
df = pd.DataFrame({
    "Symbol": ["AAPL", "GOOGL", "MSFT", "AMZN", "FB"],
    "Market Cap": [2000, 1500, 1300, 1200, 800],
    "Description": ["Apple Inc.", "Alphabet Inc.", "Microsoft Corporation", "Amazon.com Inc.", "Facebook Inc."]
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
                                   privacy_level=SpacePrivacy.Private)["space_id"]

# Open space
space = await mantis.open_space(space_id)

# Interact with space
await space.select_points(100) 
plot = await space.render_plot("Market Cap", "embed_y")

# Close when done
await space.close()
```

## Key Features

Create spaces from DataFrames or CSV files
Control visualization layout and rendering
Execute code within spaces
Generate plots and capture screenshots
Manage panels and space configuration
Support for async operations
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

## Examples

### Create Space from CSV

```python 
# Load Mantis
mantis = MantisClient("/api/proxy/", cookie)

# Set data path + types
data_path = "./StockData.csv"

data_types = {"Symbol": DataType.Title,
              "Market Cap": DataType.Numeric,
              "Description": DataType.Semantic}

# Create space
new_space_id = mantis.create_space("Stock data,", 
                                   data=data_path, 
                                   data_types=data_types,
                                   reducer=ReducerModels.UMAP,
                                   privacy_level=SpacePrivacy.PRIVATE)["space_id"]
```

### Capture Visualization

```python 
# Select 100 points
await space.select_points (100)

# Plot embed dimensions
plot = await space.render_plot ("embed_x", "embed_y")
```

### Run Analysis

```python 
code = """

computation = 6**4
print ('Hello from SDK, :P -> ' + str(computation))

"""

# Wait for code execution to finish (returns output)
await space.run_code (code)
```

### Manage Panels

```python 
await space.close_panel ("bags")
await space.close_panel ("quicksheet")
await space.close_panel ("userlogs")
```