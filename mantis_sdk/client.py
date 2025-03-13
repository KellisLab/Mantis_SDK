import uuid
from .space import Space
import requests
from typing import Optional, Dict, Any, List, Callable
from .config import ConfigurationManager
import pandas as pd
import io
import json
import time
from playwright.async_api import async_playwright
import logging

logger = logging.getLogger(__name__)

class SpacePrivacy:
    PUBLIC = "public"
    PRIVATE = "private"
    SHARED = "shared"
    
class DataType:
    Title = "title"
    Semantic = "semantic"
    Numeric = "numeric"
    Categoric = "categoric"
    Date = "date"
    Links = "links"
    CustomModel = "customModel"
    Delete = "delete"
    Connection = "connection"
    
    All = [Title, Semantic, Numeric, Categoric, Date, Links, CustomModel, Connection, Delete]
    
class AIProvider:
    OpenAI = "openai"
    HuggingFace = "huggingface"
    
class ReducerModels:
    UMAP = "UMAP"
    PCA = "PCA+UMAP"
    TSNE = "t-SNE"
    
class SpaceCreationError (Exception):
    pass

class MantisClient:
    """
    SDK for interacting with your Django API.
    """

    def __init__(self, base_url: str, cookie: str, config: Optional[ConfigurationManager] = None):
        """
        Initialize the client.

        :param base_url: Base URL of the API.
        :param token: Optional authentication token.
        """
        self.base_url = base_url.rstrip("/")

        if config is None:
            self.config = ConfigurationManager()

        self.config = config
        self.cookie = cookie
        
        if self.cookie is None:
            self._authenticate ()
            
    def _authenticate (self):
        raise NotImplementedError ("Authentication is not implemented yet.")

    def _request(self, method: str, endpoint: str, rm_slash: bool = False, **kwargs) -> Any:
        """
        Internal method to make an HTTP request.

        :param method: HTTP method (GET, POST, etc.).
        :param endpoint: API endpoint (relative to base URL).
        :return: Parsed JSON response.
        """
        def remove_slash (s: str):
            return s.lstrip('/').rstrip('/')
        
        url = f"{self.config.host}/{remove_slash(self.base_url)}/{remove_slash(endpoint)}/"
        
        # This is one of the weirdest cases I have required
        # some endpoints don't authenticate if there is a slash at the end
        # while some require it.
        if rm_slash:
            url = url.rstrip("/")
        
        headers = {"cookie": self.cookie}
        
        if method.upper() == "GET":
            headers["Cache-Control"] = "no-cache"  # Prevent caching for GET requests
            params = kwargs.get("params", {})
            params["_ts"] = str(time.time())       # Force fresh GET
            kwargs["params"] = params

        if "headers" in kwargs:
            headers.update (kwargs["headers"])
            del kwargs["headers"]

        try:
            response = requests.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"API request failed: {e}. Text: {response.text}")

    def get_spaces (self) -> Dict[str, Any]:
        """
        Get a list of all spaces.
        """
        return self._request("GET", "/api/getSpaces")
    
    def get_space_ids_by_name (self, space_name: str, privacy_levels: List[SpacePrivacy]):
        """
        Get a list of all spaces-ids that match a space name.
        """
        spaces = self.get_spaces ()
        space_ids = []
        
        for privacy_level in privacy_levels:
            privacy_spaces_subset = [space["space_id"] for space in spaces[privacy_level] if space["space_name"] == space_name]
            space_ids.extend (privacy_spaces_subset)
            
        return space_ids
    
    def _default_parameter_selection (self, parameters: dict[str, dict[str, float]]) -> str:
        return list(parameters.keys())[len(parameters) // 2]
    
    def create_space (self, space_name: str, 
                      data: pd.DataFrame | str,
                      data_types: dict[str, DataType], 
                      custom_models: Optional[list[str | None]] = None, 
                      reducer: ReducerModels = ReducerModels.UMAP,
                      privacy_level: SpacePrivacy = SpacePrivacy.PRIVATE,
                      ai_provider: AIProvider = AIProvider.OpenAI,
                      choose_variation: Callable[[dict[str, dict[str, float]]], str] = None,
                      on_recieve_id: Callable[[str], None] = None) -> Space:
        """Creates a new space in Mantis with the provided data.
        This method creates a new space using either a pandas DataFrame or a file path as input data,
        along with specified data types and optional custom models.
        Args:
            space_name (str): Name of the space to create.
            data (Union[pd.DataFrame, str]): Input data either as pandas DataFrame or file path.
            data_types (list[DataType]): List of data types for each column in the data.
            custom_models (Optional[list[str | None]], optional): List of custom models to use for each data type. 
                Must match length of data_types if provided. Defaults to None.
            reducer (ReducerModels, optional): Dimension reduction model to use. Defaults to ReducerModels.UMAP.
            privacy_level (SpacePrivacy, optional): Privacy setting for the space. Defaults to SpacePrivacy.PRIVATE.
            ai_provider (AIProvider, optional): AI provider to use for the space. Defaults to AIProvider.OpenAI.
            on_recieve_id (Callable[[str], None], optional): Callback function to call when the space ID is received. Defaults to None.
        Returns:
            Space: Dictionary containing:
                - space_id (str): Unique identifier for the created space
                - result (dict): Response from the server
        Raises:
            ValueError: If data is neither a pandas DataFrame nor a valid file path.
            AssertionError: If length of custom_models doesn't match length of data_types.
        """
        # Load file extension from the name, or set to CSV by default
        file_extension = "csv"
        
        if isinstance (data, str):
            file_extension = data.split(".")[-1]
        
        # Read file into a buffer that can be provided into req
        buffer = None
        columns = None
        
        # if the data is a dataframe
        # write it as a CSV into a bytes buffer
        # and reset the buffer so it can be read
        if isinstance(data, pd.DataFrame):
            columns = data.columns
            
            buffer = io.BytesIO ()
            data.to_csv(buffer, index=False)
            buffer.seek(0)
            
        # if it is a file path
        # read the columns, and open as rb buffer
        elif isinstance(data, str):
            columns = pd.read_csv(data, nrows=1).columns
            
            buffer = open(data, "rb")
            
        if buffer is None:
            raise ValueError ("Data must be a pandas DataFrame or a file path.")
        
        # Load the data types into the proper format
        data_types_array = []
        data_types_sanitized = []
        
        # Convert the dictionary of { column -> data_type } to a list [ data_type ]
        # that populates the missing items with DataType.Delete
        for column in columns:
            if column in data_types:
                data_types_array.append(data_types[column])
            else:
                data_types_array.append(DataType.Delete)
                
        # Convert the list of data types [ data_type ], into
        # the sanitized version that the backend expects
        for data_type in data_types_array:
            data_input = {}
            
            for possible_data_type in DataType.All:
                data_input[possible_data_type] = possible_data_type == data_type
                
            data_types_sanitized.append (data_input)
            
        # Load custom models and assert their length
        if custom_models is None:
            custom_models = [None for _ in range(len(data_types))]
            
        assert len(custom_models) == len(data_types), "Custom models must be provided for each data type, or set to None"
        
        # Generate a unique ID for the space
        space_id = str(uuid.uuid4())
        file_key = f"{space_name}-{space_id}.{file_extension}"

        if on_recieve_id is not None:
            on_recieve_id (space_id)
        
        # Create the form data
        form_data = {
            "space_id": space_id,
            "space_name": space_name,
            "is_public": str(privacy_level == SpacePrivacy.PUBLIC).lower(),
            "red_model": reducer,
            "custom_models": json.dumps(custom_models),
            "data_types": json.dumps(data_types_sanitized),
            "ai_provider": ai_provider,
            "file_key": file_key,
        }

        # Create files dict for the request
        files = {
            'file': (f'data.{file_extension}', buffer, f'text/{file_extension}')
        }

        # Send the request with both form data and file
        self._request("POST",
                      "/synthesis/landscape",
                      data=form_data,
                      files=files)
                
        choseUMAPvariations = False # Whether we have chosen the variables yet
        
        # Progress callback
        while True:
            # Get current progress, throw if error
            progress = self._request ("GET", f"synthesis/progress/{space_id}")
            logger.debug (progress)
            
            if progress["error"]:
                raise SpaceCreationError (progress["error"])
                        
            # Detects whether we need to select some params
            if progress["progress"] >= 50 and not choseUMAPvariations:
                start_time = time.time()
                parameters = None

                while True:
                    umap_variations = self._request("GET", f"synthesis/parameters/{space_id}")

                    if "umap_variations" in umap_variations and "parameters" in umap_variations["umap_variations"]:
                        parameters = umap_variations["umap_variations"]["parameters"]
                        break

                    if time.time() - start_time > 3600:
                        raise TimeoutError("Timeout waiting for UMAP parameters")
                    
                    time.sleep(1)
                
                # Choose parameter based on default or custom function
                chosen_parameter = None
                if choose_variation is None:
                    chosen_parameter = self._default_parameter_selection (parameters)
                else:
                    chosen_parameter = choose_variation (parameters)
                
                # Select params
                self._request ("POST", 
                               f"synthesis/landscape/{space_id}/select-umap/{chosen_parameter}",
                               rm_slash=True,
                               json={"selected_variation": chosen_parameter})
                
                choseUMAPvariations = True
                
            # Break once finished
            if progress["progress"] == 100:
                break
            
            # TODO: Delete this ASAP, this is just a fix for a bug in the synthesis Pipeline
            # TODO: this will be able to be deleted with no other changes
            if progress["progress"] == 0 and choseUMAPvariations:
                break
            
            time.sleep (1)
            
        return {"space_id": space_id}

    async def open_space(self, space_id: str) -> "Space":
        """
        Asynchronously open a space by ID.
        """
        return await Space.create(
            space_id, 
            _request=self._request, 
            cookie=self.cookie, 
            config=self.config
        )

    async def __aenter__(self):
        """
        Async context manager entry
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit
        """
        # Clean up if needed
        pass