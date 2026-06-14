"""resolve a map to its project, create a notebook, run a cell, and save the plot it makes.

requires the notebook (kernel) docker stack to be running. auth via MANTIS_COOKIE or
MANTIS_INTERNAL_USER_ID (the user the notebook/session is bound to)."""
import os

from mantis_sdk import ConfigurationManager, MantisClient

config = ConfigurationManager()
config.internal_user_id = os.getenv("MANTIS_INTERNAL_USER_ID")

client = MantisClient("/api/proxy/", cookie=os.getenv("MANTIS_COOKIE"), config=config)

MAP_ID = os.environ["MANTIS_MAP_ID"]
USER_ID = os.environ["MANTIS_USER_ID"]

project_id = client.notebooks.resolve_map_to_project(MAP_ID)
nb = client.notebooks.create(project_id, name="SDK Test", user_id=USER_ID)

text_cell = nb.add_cell("print('points:', len(maps[0].points))")
print(text_cell.execute())
print("text output:\n", text_cell.text)

plot_cell = nb.add_cell(
    "import matplotlib.pyplot as plt\n"
    "plt.plot([1, 2, 3], [3, 1, 2]); plt.title('demo'); plt.show()"
)
plot_cell.execute()

png = plot_cell.image_png_bytes()
if png:
    with open("output_plot.png", "wb") as f:
        f.write(png)
    print("saved output_plot.png")

# snapshot the kernel state.
print("checkpoint:", nb.checkpoint("after-plot"))
