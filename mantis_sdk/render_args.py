from typing import Dict

class RenderArgs:
    def __init__ (self, args: Dict[str, str] = {}, **kwargs):
        self.args = args
        self.args.update (kwargs)