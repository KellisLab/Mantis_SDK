"""browser-side automation of a mantis space via playwright.

a Space drives the frontend by calling window.sdkCommand(name, args). it wraps a curated
slice of the frontend executors with typed methods, and exposes command() as a generic
escape hatch for any executor not yet wrapped."""
from __future__ import annotations

import asyncio
import http.cookies
from typing import Any

from playwright.async_api import async_playwright

from .config import ConfigurationManager


class Space:
    def __init__(self, space_id: str, _request, cookie: str, config: ConfigurationManager | None = None):
        self.space_id = space_id
        self.cookie = cookie
        self.config = config
        self._request = _request
        self.headless = self._get_render_arg("headless")

        # initialized in create().
        self.playwright = None
        self.browser = None
        self.page = None

    @classmethod
    async def create(cls, space_id: str, _request, cookie: str,
                     config: ConfigurationManager | None = None, colab: bool = False):
        """factory: launch a browser, seed cookies, and navigate to the space."""
        if config is None:
            config = ConfigurationManager()

        instance = cls(space_id, _request, cookie, config)
        instance.playwright = await async_playwright().start()

        browser_args = ["--start-maximized"]
        if colab:
            browser_args.extend(["--no-sandbox", "--disable-setuid-sandbox"])

        instance.browser = await instance.playwright.chromium.launch(
            headless=instance.headless, args=browser_args
        )
        await instance._init_space()
        return instance

    def _get_render_arg(self, key: str):
        return self.config.render_args.args.get(key)

    async def _init_space(self):
        """seed cookies, open the space page, and wait for the configured ready flag."""
        if self.page:
            return

        mc = http.cookies.SimpleCookie()
        mc.load(self.cookie)

        context = await self.browser.new_context(no_viewport=not self.headless)
        cookies = [
            {
                "name": morsel.key,
                "value": morsel.value,
                "domain": self.config.domain,
                "path": "/",
                "httpOnly": False,
                "secure": True,
            }
            for morsel in mc.values()
        ]
        await context.add_cookies(cookies)

        self.page = await context.new_page()
        if self.headless:
            viewport = self._get_render_arg("viewport")
            await self.page.set_viewport_size({"width": viewport["width"], "height": viewport["height"]})

        await self.page.goto(f"{self.config.host}/space/{self.space_id}/", timeout=self.config.timeout)
        await self._apply_init_render_args()

        wait_for = getattr(self.config, "wait_for", "isLoaded")
        await self.page.wait_for_function(f"() => window.{wait_for} === true", timeout=self.config.timeout)

        # let points render after data loads.
        await asyncio.sleep(5)

    async def _apply_init_render_args(self):
        pass

    async def _execute_sdk(self, command: str, args: list) -> Any:
        """invoke a single window.sdkCommand executor with positional args."""
        return await self.page.evaluate(
            "async ([command, args]) => await window.sdkCommand(command, args)",
            [command, args],
        )

    # generic escape hatch — call any executor not explicitly wrapped below.
    async def command(self, name: str, *args: Any) -> Any:
        """call any frontend executor by name with positional args."""
        return await self._execute_sdk(name, list(args))

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    async def get_available_dimensions(self) -> Any:
        return await self._execute_sdk("getAvailableDimensions", [])

    async def get_tree_status(self) -> Any:
        """top-level cluster tree."""
        return await self._execute_sdk("getTreeStatus", [])

    async def get_cluster_children(self, cluster_id: str) -> Any:
        return await self._execute_sdk("getClusterChildren", [cluster_id])

    # ------------------------------------------------------------------
    # selection & bags
    # ------------------------------------------------------------------
    async def select_point(self, point_id: str) -> Any:
        return await self._execute_sdk("selectPoint", [point_id])

    async def add_bag(self, bag_name: str, point_ids: list[str], space_id: str | None = None) -> Any:
        return await self._execute_sdk("addBag", [bag_name, point_ids, space_id or self.space_id])

    async def get_bags(self, correlation_id: str = "") -> Any:
        return await self._execute_sdk("getBags", [correlation_id])

    async def get_bag_contents(self, bag_id: str, limit: int | None = None,
                               offset: int | None = None, random_sample: bool = False,
                               fields: list[str] | None = None) -> Any:
        return await self._execute_sdk("getBagContents", [bag_id, limit, offset, random_sample, fields])

    async def create_bag_from_cluster(self, cluster_id: str, bag_name: str | None = None) -> Any:
        return await self._execute_sdk("createBagFromCluster", [cluster_id, bag_name])

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    async def general_search(self, query: str, search_type: str = "semantic", limit: int = 25,
                             cluster_id: str | None = None, bag_id: str | None = None) -> Any:
        return await self._execute_sdk("generalSearch", [query, search_type, limit, cluster_id, bag_id])

    async def get_point_details(self, point_ids: list[str], fields: list[str] | None = None) -> Any:
        return await self._execute_sdk("getPointDetails", [point_ids, fields])

    # ------------------------------------------------------------------
    # map transforms
    # ------------------------------------------------------------------
    async def map_color_by(self, data: dict, map_id: str | None = None) -> Any:
        return await self._execute_sdk("map_color_by", [data, map_id])

    async def map_set_z(self, data: dict, map_id: str | None = None) -> Any:
        return await self._execute_sdk("map_set_z", [data, map_id])

    async def map_reset(self, data: dict | None = None, map_id: str | None = None) -> Any:
        return await self._execute_sdk("map_reset", [data or {}, map_id])

    # ------------------------------------------------------------------
    # plotting & screenshots (existing surface, retained)
    # ------------------------------------------------------------------
    async def render_plot(self, dimension_x, dimension_y) -> bytes:
        """plot two dimensions and return a png screenshot of the plot element."""
        await self._execute_sdk("setPlotSize", [600, 600])
        await self._execute_sdk("setPlotVariables", [dimension_x, dimension_y])
        selector = await self._execute_sdk("getPlotSelect", [])
        await asyncio.sleep(5)
        screenshot = await self._screenshot(selector)
        await self._execute_sdk("setPlotSize", [100, 100])
        return screenshot

    async def _screenshot(self, selector: str | None = None) -> bytes:
        if selector:
            return await self.page.locator(selector).screenshot()
        return await self.page.screenshot()

    async def select_points(self, n: int) -> Any:
        return await self._execute_sdk("selectPoints", [n])

    async def capture(self) -> bytes:
        return await self._screenshot()

    async def open_panel(self, panel_id: str) -> Any:
        return await self._execute_sdk("openPanel", [panel_id])

    async def close_panel(self, panel_id: str) -> Any:
        return await self._execute_sdk("closePanel", [panel_id])

    async def run_code(self, code: str) -> Any:
        return await self._execute_sdk("execCode", [code])

    async def get_mcp_session(self) -> Any:
        return await self.page.evaluate("() => window.__MCP_SESSION_ID")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def __aenter__(self) -> Space:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
