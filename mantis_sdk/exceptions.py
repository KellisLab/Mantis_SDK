class MantisSDKError(Exception):
    """Base exception for Mantis SDK."""
    pass

class APIRequestError(MantisSDKError):
    """Exception raised for API request errors."""
    pass
