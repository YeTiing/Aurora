# Plugin: auto-format
def on_startup():
    print("[auto-format] loaded — will format on save")

def on_shutdown():
    print("[auto-format] unloaded")

def on_tool_call(tool_name, params):
    return None

def on_response(response):
    return response