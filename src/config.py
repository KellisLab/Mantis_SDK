from render_args import RenderArgs

HOST = "http://localhost:3000"
DOMAIN = "localhost"
TIMEOUT = 60000

defaultRenderArgs = RenderArgs ({
    "headless": True,
    "viewport": {"width": 1920, "height": 1080}
})