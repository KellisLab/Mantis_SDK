from playwright.async_api import async_playwright
from typing import Dict, Optional
import http.cookies
from .render_args import RenderArgs
from .config import ConfigurationManager
import asyncio

class Space:
    def __init__(self, space_id: str, _request, cookie: str, config: Optional[ConfigurationManager] = None, colab: bool = False):
        # Space args
        self.space_id = space_id
        self.cookie = cookie
        self.config = config
        self._request = _request
        self.headless = self._get_render_arg ("headless")
        self.colab = colab
        
        # These will be initialized in create()
        self.playwright = None
        self.browser = None
        self.page = None

    @classmethod
    async def create(cls, space_id: str, _request, cookie: str, config: Optional[ConfigurationManager] = None, colab: bool = False):
        """Factory method to create and initialize a Space instance"""
        if config is None:
            config = ConfigurationManager()

        instance = cls(space_id, _request, cookie, config, colab)
        instance.playwright = await async_playwright().start()
        
        browser_args = ["--start-maximized"]
        if colab:
            browser_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])
        
        instance.browser = await instance.playwright.chromium.launch(headless=instance.headless, args=browser_args)
        await instance._init_space()
        return instance

    def _get_render_arg(self, key: str):
        """
        Retrieves rendering argument value for a given key.
        This method returns a rendering argument value either from the instance's render_args
        or from defaultRenderArgs if not found in instance's render_args.
        Args:
            key (str): The key for the rendering argument to retrieve.
        Returns:
            The value associated with the key from render_args or defaultRenderArgs.
            Returns None if the key is not found in either source.
        """
        return self.config.render_args.args.get (key)
        
    async def _init_space(self):
        """
        Initializes a new browser page within the space context.
        This method sets up a new page in the browser context with the following steps:
        1. Creates a new cookie from the stored cookie string
        2. Initializes a new browser context
        3. Converts cookie morsels to the format required by playwright
        4. Adds cookies to the context
        5. Creates and navigates to a new page for the space
        6. Waits for the loading state
        7. Allows time for points to render
        
        Raises:
            TimeoutError: If page navigation or loading exceeds TIMEOUT value
        """
        if not self.page:
            # Load cookie string into an object
            mc = http.cookies.SimpleCookie()
            mc.load (self.cookie)
            
            # Create new context and register cookies
            context = await self.browser.new_context(no_viewport=not self.headless)
            
            cookies = []
            
            for morsel in mc.values ():
                cookies.append ({
                    "name": morsel.key,
                    "value": morsel.value,
                    "domain": self.config.domain,
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                })
                
            await context.add_cookies (cookies)

            print ("Added cookies\n", cookies)
            
            # Goto page
            self.page = await context.new_page()

            print ("First screenshot\n", (await self._screenshot()))
            
            if self.headless:
                await self.page.set_viewport_size ({"width": self._get_render_arg("viewport")["width"], 
                                                    "height": self._get_render_arg("viewport")["height"]})
            
            await self.page.goto (f"{self.config.host}/space/{self.space_id}/",
                            timeout=self.config.timeout)
            
            await self._apply_init_render_args ()
            await asyncio.sleep (5)
            print ("Second screenshot\n", (await self._screenshot()))

            wait_for = self.config.wait_for if hasattr(self.config, 'wait_for') else "isLoaded"

            print ("Waiting for", wait_for)
        
            wait_for_script = f"""() => window.{wait_for} === true"""

            print (wait_for_script)

            if not self.colab:            
                # Wait until the exposed loading value is true
                await self.page.wait_for_function (wait_for_script,
                                         timeout=self.config.timeout)
            else:
                while not (await self.page.evaluate(wait_for_script, [])):
                    print ("Sleeping once more")
                    await asyncio.sleep (1)

            print ("Okay, yay.")
            
            # Let points render after data is loaded
            await asyncio.sleep (1)
            
    async def _apply_init_render_args(self):
        pass
            
    async def _execute_sdk(self, command: str, args: list):
        """
        Executes an exposed SDK command in the browser context.
        Args:
            command (str): The SDK command to execute
            args (list): List of arguments to pass to the SDK command
        Returns:
            The result of the SDK command execution
        """
        # Execute exposed SDK command 
        result = await self.page.evaluate (
            """async ([command, args]) => await window.sdkCommand(command, args)""",
            [command, args])
        
        return result
    
    # TODO: Implement a command to retrieve the possible plottable dimensions
    
    async def render_plot(self, dimension_x, dimension_y):
        """
        Plots the data in the space context.
        Args:
            dimension_x (str): The name of the column to plot on the x-axis.
            dimension_y (str): The name of the column to plot on the y-axis.
        Returns:
            bytes: A bytes object containing the plot image data in PNG format.
        """
        await self._execute_sdk ("setPlotSize", [600, 600])
        await self._execute_sdk ("setPlotVariables", [dimension_x, dimension_y])
        selector = await self._execute_sdk ("getPlotSelect", [])
        
        await asyncio.sleep (5)
        
        screenshot = await self._screenshot(selector)
        await self._execute_sdk ("setPlotSize", [100, 100])
        
        return screenshot
    
    async def _screenshot(self, selector: Optional[str] = None):
        """
        Take a screenshot of the page or a specific element.

        Args:
            selector (str, optional): CSS selector of the element to screenshot. 
                If None, screenshots entire page.
            scale (float, optional): Scale factor for the screenshot resolution.
                Default is 1.0. Higher values increase resolution.

        Returns:
            bytes: The screenshot as a bytes object.
        """
        if selector:
            return await self.page.locator(selector).screenshot()

        return await self.page.screenshot()
    
    async def select_points(self, n: int):
        """
        Select n points in the space.
        """
        return await self._execute_sdk ("selectPoints", [n])
    
    async def capture(self):
        """
        Captures and returns a screenshot of the current page.

        Returns:
            bytes: A bytes object containing the screenshot image data in PNG format.
        """
        return await self._screenshot()
    
    async def open_panel(self, panel_id: str):
        """
        Opens a panel within the space context.

        Args:
            panel_id (str): The ID of the panel to open.
        """
        return await self._execute_sdk ("openPanel", [panel_id])
        
    async def close_panel(self, panel_id: str):
        """
        Closes a panel within the space context.

        Args:
            panel_id (str): The ID of the panel to close.
        """
        return await self._execute_sdk ("closePanel", [panel_id])
    
    async def run_code(self, code: str):
        """
        Executes a code snippet within the space context.

        Args:
            code (str): The code snippet to execute.
        """
        return await self._execute_sdk ("execCode", [code])
    
    async def get_mcp_session(self):
        """
        Retrieves the MCP session ID from the browser's window object.

        Returns:
            str: The MCP session ID from window.__MCP_SESSION_ID, or None if not available.
        """
        return await self.page.evaluate("() => window.__MCP_SESSION_ID")

    async def close(self):
        """
        Closes the browser instance and stops the Playwright session.

        This method performs cleanup by closing the active browser window
        and terminating the underlying Playwright process.
        """
        await self.browser.close()
        await self.playwright.stop()
