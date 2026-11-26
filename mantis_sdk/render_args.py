class RenderArgs:
    def __init__(self, args: dict[str, str] | None = None, **kwargs):
        # copy caller's dict (or create a fresh one) to avoid shared default / caller mutation.
        self.args = dict(args) if args is not None else {}
        self.args.update (kwargs)
