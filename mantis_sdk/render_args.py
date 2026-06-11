

class RenderArgs:
    def __init__ (self, args: dict[str, str] = {}, **kwargs):
        self.args = args
        self.args.update (kwargs)
