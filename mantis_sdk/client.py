import requests
from typing import Optional, Dict, Any

class MantisClient:
    """
    SDK for interacting with your Django API.
    """

    def __init__(self, base_url: str, token: Optional[str] = None):
        """
        Initialize the client.

        :param base_url: Base URL of the API.
        :param token: Optional authentication token.
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """
        Internal method to make an HTTP request.

        :param method: HTTP method (GET, POST, etc.).
        :param endpoint: API endpoint (relative to base URL).
        :return: Parsed JSON response.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"API request failed: {e}")


    def get_email_by_user_id(self, user_id: str) -> Dict[str, Any]:
        """
        Fetch the email for a user by user ID.

        :param user_id: The user ID.
        :return: User email data.
        """
        return self._request("GET", f"get_email_by_user_id?user_id={user_id}")

    def rename_space(self, space_id: str, new_name: str) -> Dict[str, Any]:
        """
        Rename a space by ID.

        :param space_id: The space ID.
        :param new_name: The new name for the space.
        :return: Response data.
        """
        return self._request("PATCH", f"rename_space/{space_id}", json={"new_name": new_name})

    def update_shared_users(self, space_id: str, user_ids: list) -> Dict[str, Any]:
        """
        Update shared users for a space.

        :param space_id: The space ID.
        :param user_ids: List of user IDs to share with.
        :return: Response data.
        """
        return self._request("POST", f"update_shared_users/{space_id}", json={"user_ids": user_ids})

    def get_shared_spaces(self) -> Dict[str, Any]:
        """
        Get spaces shared with the current user.

        :return: List of shared spaces.
        """
        return self._request("GET", "get_shared_spaces")

    def create_space(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new space.

        :param data: Data for creating the space.
        :return: Response data.
        """
        return self._request("POST", "create_space", json=data)

    def update_space_visibility(self, space_id: str) -> Dict[str, Any]:
        """
        Toggle the visibility of a space.

        :param space_id: The space ID.
        :return: Response data.
        """
        return self._request("POST", f"update_space_visibility/{space_id}")

    def delete_space(self, space_id: str) -> Dict[str, Any]:
        """
        Delete a space by ID.

        :param space_id: The space ID.
        :return: Response data.
        """
        return self._request("DELETE", f"delete_space_DB/{space_id}")

    def update_space_metadata(self, space_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update metadata for a space.

        :param space_id: The space ID.
        :param metadata: Metadata to update.
        :return: Response data.
        """
        return self._request("PATCH", f"update_space_metadata/{space_id}", json={"metadata": metadata})

    def get_space_metadata(self, space_id: str) -> Dict[str, Any]:
        """
        Get metadata for a space.

        :param space_id: The space ID.
        :return: Metadata.
        """
        return self._request("GET", f"get_space_metadata/{space_id}")

    def get_space_progress(self, space_id: str) -> Dict[str, Any]:
        """
        Get progress for a space creation task.

        :param space_id: The space ID.
        :return: Progress data.
        """
        return self._request("GET", f"get_space_progress/{space_id}")

    def upload_images(self, files: list, space_name: str, is_public: bool) -> Dict[str, Any]:
        """
        Upload images to a space.

        :param files: List of file objects to upload.
        :param space_name: Name of the space.
        :param is_public: Whether the space is public.
        :return: Response data.
        """
        files_data = [("files", (file.name, file, "image/jpeg")) for file in files]
        data = {"spaceName": space_name, "public": str(is_public).lower()}
        return self._request("POST", "upload_images", files=files_data, data=data)

    def get_shared_spaces(self) -> Dict[str, Any]:
        """
        Get all spaces shared with the current user.

        :return: List of shared spaces.
        """
        return self._request("GET", "get_shared_spaces")

