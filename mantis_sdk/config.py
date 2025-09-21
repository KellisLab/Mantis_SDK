from .render_args import RenderArgs
import os

class ConfigurationManager:
    def __init__(self):
        # Default values
        self.host = os.getenv("MANTIS_HOST", "http://localhost:3000")
        self.backend_host = os.getenv("MANTIS_BACKEND_HOST", "http://localhost:8000")
        self.domain = os.getenv("MANTIS_DOMAIN", "localhost")
        self.timeout = int(os.getenv("MANTIS_TIMEOUT", "60000"))
        self.render_args = RenderArgs({
            "headless": True,
            "viewport": {"width": 1920, "height": 1080}
        })

    def update(self, config_dict: dict):
        """Update multiple configuration values at once"""
        for key, value in config_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)
