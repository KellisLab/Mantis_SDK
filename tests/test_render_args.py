from mantis_sdk import RenderArgs


def test_fresh_instance_is_empty_after_previous_kwargs():
    # regression guard: a shared mutable default dict would leak prior kwargs.
    first = RenderArgs(theme="dark")
    second = RenderArgs()
    assert second.args == {}
    assert second.args is not first.args


def test_caller_dict_is_not_mutated():
    # kwargs update must not write into the dict passed by the caller.
    caller = {"headless": "true"}
    RenderArgs(caller, theme="dark")
    assert caller == {"headless": "true"}


def test_kwargs_override_dict_entries():
    # pin existing precedence: explicit kwargs override matching keys in args.
    ra = RenderArgs({"theme": "light", "mode": "2d"}, theme="dark")
    assert ra.args == {"theme": "dark", "mode": "2d"}
