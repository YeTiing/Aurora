"""Aurora API startup verification."""
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(str(Path(__file__).parent))

from backend.api import app
from fastapi.testclient import TestClient


def check_routes():
    print("=" * 64)
    print("  Aurora API Routes")
    print("=" * 64)
    routes = app.routes
    print(f"\n  Total: {sum(1 for r in routes if hasattr(r,'path'))} routes")
    groups = {}
    for route in routes:
        if not hasattr(route, "path"):
            continue
        parts = route.path.split("/")
        prefix = parts[1] if len(parts) > 1 else "/"
        groups.setdefault(prefix, []).append(route.path)

    expected = {"agents","agents-md","approval","auth","browser","chat","checkpoint",
                "config","context","cron","detective","files","goal","health",
                "heartbeat","llm","marketplace","mcp","memory","models",
                "observability","plugins","presets","processes","prompts",
                "providers","rag","re","sentry","sessions","settings","skins",
                "soul","storage","tasks","threads","tools","ws"}
    found = set(groups.keys()) - {"docs","openapi.json","redoc"}
    missing = expected - found
    extra = found - expected
    if not missing:
        print(f"  All {len(expected)} route groups present")
    if extra:
        print(f"  Extra: {sorted(extra)}")
    if missing:
        print(f"  MISSING: {sorted(missing)}")

    # Method stats
    mc = {}
    for route in routes:
        if hasattr(route, "methods"):
            for m in route.methods:
                mc[m] = mc.get(m, 0) + 1
    print(f"  Methods: GET={mc.get('GET',0)} POST={mc.get('POST',0)} "
          f"PUT={mc.get('PUT',0)} DELETE={mc.get('DELETE',0)}")
    return not missing


def check_health():
    print("\n" + "=" * 64)
    print("  Health Check")
    print("=" * 64)
    client = TestClient(app)
    ok = 0
    for path, label in [("/health", "health"), ("/docs", "docs")]:
        try:
            r = client.get(path)
            print(f"  GET {path}  HTTP {r.status_code}  {'OK' if r.status_code == 200 else 'WARN'}")
            if r.status_code == 200:
                ok += 1
        except Exception as e:
            print(f"  GET {path}  ERR: {e}")

    # OpenAPI schema - try but don't fail
    try:
        r = client.get("/openapi.json")
        if r.status_code == 200:
            s = r.json()
            print(f"  openapi.json  OK  ({len(s.get('paths',{}))} paths)")
            ok += 1
        else:
            print(f"  openapi.json  SKIP (non-blocking)")
    except Exception:
        print(f"  openapi.json  SKIP (non-blocking)")
    return ok >= 2


def check_endpoints():
    print("\n" + "=" * 64)
    print("  Key Endpoints")
    print("=" * 64)
    client = TestClient(app)
    checks = [
        "/health", "/docs", "/soul", "/tools", "/config",
        "/sessions", "/models", "/settings", "/plugins",
        "/memory/stats", "/agents/tree", "/cron/stats",
        "/skins", "/marketplace", "/observability/stats",
        "/rag/search", "/auth/status",
    ]
    ok = 0
    for path in checks:
        try:
            r = client.get(path)
            if r.status_code < 500:
                ok += 1
            else:
                print(f"  GET {path}  HTTP {r.status_code}")
        except Exception as e:
            print(f"  GET {path}  ERR")
    print(f"\n  Accessible: {ok}/{len(checks)}")


if __name__ == "__main__":
    try:
        r = check_routes()
        h = check_health()
        check_endpoints()
        print("\n" + "=" * 64)
        if r and h:
            print("  Aurora API verification PASSED")
        else:
            print("  Aurora API verification: issues found")
        print("=" * 64)
    except Exception as e:
        print(f"\n  FATAL: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
