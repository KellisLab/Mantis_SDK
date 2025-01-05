# SDK for Your Django API

## Overview
This SDK provides a Python interface for interacting with your Django-based API. It simplifies the process of making requests to various endpoints, handling authentication, and parsing responses.

## Features
- Fetch user emails by ID.
- Rename spaces.
- Update shared users for a space.
- Retrieve shared spaces.
- Create, update, and delete spaces.
- Upload images to spaces.
- Manage metadata and visibility for spaces.
- Monitor space creation progress.

## Installation

Install the SDK using pip:

```bash
pip install your-sdk-name
```

## Usage

### Initialization

```python
from your_sdk_name import YourAPIClient

# Initialize the client
client = YourAPIClient(base_url="https://api.example.com", token="your-auth-token")
```

### Available Methods

#### Fetch Email by User ID

```python
email_data = client.get_email_by_user_id(user_id="12345")
print(email_data)
```

#### Rename Space

```python
response = client.rename_space(space_id="space-id", new_name="New Space Name")
print(response)
```

#### Update Shared Users

```python
response = client.update_shared_users(space_id="space-id", user_ids=["user1", "user2"])
print(response)
```

#### Retrieve Shared Spaces

```python
shared_spaces = client.get_shared_spaces()
print(shared_spaces)
```

#### Create Space

```python
space_data = {
    "space_name": "My Space",
    "is_public": True,
    "data_types": [{"name": "field1", "semantic": True}],
    "file": open("data.csv", "rb")
}
response = client.create_space(data=space_data)
print(response)
```

#### Update Space Visibility

```python
response = client.update_space_visibility(space_id="space-id")
print(response)
```

#### Delete Space

```python
response = client.delete_space(space_id="space-id")
print(response)
```

#### Update Space Metadata

```python
metadata = {"key": "value"}
response = client.update_space_metadata(space_id="space-id", metadata=metadata)
print(response)
```

#### Get Space Metadata

```python
metadata = client.get_space_metadata(space_id="space-id")
print(metadata)
```

#### Get Space Progress

```python
progress = client.get_space_progress(space_id="space-id")
print(progress)
```

#### Upload Images

```python
files = [open("image1.jpg", "rb"), open("image2.jpg", "rb")]
response = client.upload_images(files=files, space_name="My Space", is_public=True)
print(response)
```

## Contributing

Contributions are welcome! Please submit issues and pull requests to improve the SDK.

## License

This SDK is licensed under the MIT License. See the LICENSE file for details.

