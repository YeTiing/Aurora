# Aurora CLI v2 - Enhanced terminal interface
"""Rich CLI / History / Tab-completion / Progress / Session management"""
from __future__ import annotations
import argparse, asyncio, json, sys, os, re, time, readline, atexit, subprocess
from pathlib import Path
from typing import Optional

VERSION = "0.2.0"

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Colors ---
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"
    WHITE = "\033[37m"

    @staticmethod
    def color(text: str, color: str) -> str:
        return f"{color}{text}{Colors.RESET}"

    @staticmethod
    def bold(text: str) -> str: return f"{Colors.BOLD}{text}{Colors.RESET}"
    @staticmethod
    def dim(text: str) -> str: return f"{Colors.DIM}{text}{Colors.RESET}"
    @staticmethod
    def cyan(text: str) -> str: return f"{Colors.CYAN}{text}{Colors.RESET}"
    @staticmethod
    def green(text: str) -> str: return f"{Colors.GREEN}{text}{Colors.RESET}"
    @staticmethod
    def yellow(text: str) -> str: return f"{Colors.YELLOW}{text}{Colors.RESET}"
    @staticmethod
    def red(text: str) -> str: return f"{Colors.RED}{text}{Colors.RESET}"
    @staticmethod
    def magenta(text: str) -> str: return f"{Colors.MAGENTA}{text}{Colors.RESET}"


C = Colors

# --- History file ---
HISTORY_FILE = Path.home() / ".aurora" / "cli_history"
HISTORY_LENGTH = 1000


def setup_readline():
    """Configure readline history + completion"""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        readline.read_history_file(str(HISTORY_FILE))
    except (FileNotFoundError, PermissionError):
        pass

    readline.set_history_length(HISTORY_LENGTH)

    COMMANDS = [
        "/help", "/config", "/tools", "/stats", "/skills",
        "/rag", "/sessions", "/clear", "/quit", "/q",
        "/export", "/model", "/workspace",
    ]

    def completer(text: str, state: int) -> Optional[str]:
        line = readline.get_line_buffer()
        if text.startswith("/"):
            matches = [c for c in COMMANDS if c.startswith(text)]
            return matches[state] if state < len(matches) else None
        if state == 0:
            try:
                import glob
                expanded = os.path.expanduser(text)
                matches = glob.glob(expanded + "*")
                matches = [m + (os.sep if os.path.isdir(m) else "") for m in matches]
                completer._matches = matches
            except Exception:
                completer._matches = []
        return completer._matches[state] if state < len(completer._matches) else None

    completer._matches = []
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")

    atexit.register(save_history)


def save_history():
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except Exception:
        pass


# --- Formatting ---
def format_table(headers: list[str], rows: list[list[str]], col_widths: list[int] | None = None) -> str:
    if not col_widths:
        col_widths = [max(len(str(r[i])) if i < len(r) else 0 for r in [headers] + rows) + 2 for i in range(len(headers))]

    sep = "+" + "+".join("-" * w for w in col_widths) + "+"
    lines = [sep]
    lines.append("|" + "".join(f" {C.bold(headers[i]):<{col_widths[i]-1}}" for i in range(len(headers))) + "|")
    lines.append(sep)

    for row in rows:
        lines.append("|" + "".join(f" {str(row[i]) if i < len(row) else '':<{col_widths[i]-1}}" for i in range(len(headers))) + "|")
    lines.append(sep)
    return "\n".join(lines)


def format_tool_specs(tools: list) -> str:
    headers = ["Tool", "Description", "Category"]
    rows = []
    for t in tools:
        if hasattr(t, 'name'):
            rows.append([t.name, t.description[:55], getattr(t, 'category', 'general')])
        elif isinstance(t, dict):
            rows.append([t.get("name", ""), t.get("description", "")[:55], t.get("category", "general")])
    return format_table(headers, rows, [22, 55, 14])


def spinner_task(task_desc: str, coro):
    """Show spinner during async task"""
    class Spinner:
        def __init__(self, desc: str):
            self.desc = desc
            self.frames = ["◐", "◓", "◑", "◕", "◔", "◒", "◐", "◓", "◑", "◕"]
            self.idx = 0
            self._task = None

        async def spin(self):
            while True:
                print(f"\r  {C.cyan(self.frames[self.idx])} {self.desc}...", end="", flush=True)
                self.idx = (self.idx + 1) % len(self.frames)
                await asyncio.sleep(0.08)

        async def __aenter__(self):
            self._task = asyncio.ensure_future(self.spin())
            return self

        async def __aexit__(self, *args):
            if self._task:
                self._task.cancel()
            print("\r" + " " * (len(self.desc) + 20), end="\r", flush=True)

    return Spinner(task_desc)


# --- Banner ---
def show_banner():
    width = 50
    print(f"\n{C.bold(C.magenta('  ┌' + '─' * (width-2) + '┐'))}")
    print(f"{C.bold(C.magenta('  │'))}{'Aurora AI Coding Agent'.center(width-2)}{C.bold(C.magenta('│'))}")
    print(f"{C.bold(C.magenta('  │'))}{C.dim(f'v{VERSION}'.center(width-2))}{C.bold(C.magenta('│'))}")
    print(f"{C.bold(C.magenta('  └' + '─' * (width-2) + '┘'))}")
    print(f"\n  {C.dim('Type')} {C.cyan('/help')} {C.dim('for commands,')} {C.cyan('/quit')} {C.dim('to exit')}")
    print(f"  {C.dim('Tab to autocomplete · Ctrl+C to interrupt')}")
    print()


# --- Session manager ---
class SessionManager:
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._active: str = ""

    def create(self, name: str = "") -> str:
        sid = f"session_{len(self._sessions)+1}"
        self._sessions[sid] = {
            "id": sid, "name": name or f"Session {len(self._sessions)+1}",
            "created": time.time(), "messages": 0, "tokens": 0,
        }
        self._active = sid
        return sid

    @property
    def active(self) -> str:
        return self._active or self.create()

    def list_all(self) -> list[dict]:
        return list(self._sessions.values())

    def record_msg(self, sid: str, tokens: int = 0):
        if sid in self._sessions:
            self._sessions[sid]["messages"] += 1
            self._sessions[sid]["tokens"] += tokens


sessions = SessionManager()


# --- Command handler (interactive slash commands) ---
async def handle_command(cmd: str, cfg, tool_registry, graph):
    parts = cmd.split()
    c = parts[0].lower()

    if c == "/help":
        print(f"""
  {C.bold('Aurora CLI Commands:')}

  {C.bold('General:')}
    {C.cyan('/help')}                 Show this help
    {C.cyan('/config')}               Show current configuration
    {C.cyan('/tools')}                List available tools
    {C.cyan('/stats')}                Show agent stats & metrics
    {C.cyan('/sessions')}             List active sessions
    {C.cyan('/skills')}               List loaded skills
    {C.cyan('/rag search')} <q>     Search indexed code
    {C.cyan('/export')} <file>      Export session to file
    {C.cyan('/clear')}              Clear current session
    {C.cyan('/quit')}, {C.cyan('/q')}           Exit Aurora

  {C.bold('Keyboard:')}
    {C.dim('Tab')}            Autocomplete commands/paths
    {C.dim('Ctrl+C')}         Interrupt current operation
    {C.dim('Ctrl+D')}         Exit
    {C.dim('Up/Down')}        Navigate history
""")

    elif c == "/config":
        conf = cfg.all()
        print(f"\n  {C.bold('Configuration:')}")
        for key, val in sorted(conf.items()):
            if isinstance(val, dict):
                print(f"  {C.cyan(key)}:")
                for k, v in val.items():
                    v_display = "****" if "key" in k.lower() or "secret" in k.lower() else str(v)
                    print(f"    {k}: {C.dim(v_display)}")
            else:
                print(f"  {key}: {C.dim(val)}")

    elif c == "/tools":
        tools = tool_registry.list_tools()
        print(f"\n{format_tool_specs(tools)}")

    elif c == "/stats":
        print(f"\n  {C.bold('Agent Stats:')}")
        stats = graph.stats()
        for k, v in stats.items():
            print(f"    {C.cyan(k)}: {v}")

        from backend.observability import metrics
        print(f"\n  {C.bold('Metrics:')}")
        snap = metrics.snapshot()
        for mtype, items in snap.items():
            if items:
                print(f"  {C.yellow(mtype)}:")
                for name, m in items.items():
                    if mtype == "histograms":
                        print(f"    {name}: avg={m.get('avg',0)}ms p50={m.get('p50',0)} p95={m.get('p95',0)}")
                    else:
                        print(f"    {name}: {m.get('value', '?')}")

    elif c == "/sessions":
        slist = sessions.list_all()
        if not slist:
            print(f"  {C.dim('No sessions recorded yet.')}")
        else:
            headers = ["ID", "Name", "Messages", "Created"]
            rows = []
            for s in slist:
                created = time.strftime("%H:%M:%S", time.localtime(s["created"]))
                is_active = ">" if s["id"] == sessions.active else " "
                rows.append([f"{is_active}{s['id']}", s["name"], str(s["messages"]), created])
            print(f"\n{format_table(headers, rows, [14, 20, 10, 14])}")

    elif c == "/skills":
        from backend.skills import skill_manager
        skills = skill_manager.list_all()
        if not skills:
            print(f"  {C.dim('No skills loaded.')}")
        else:
            headers = ["Name", "Description", "Triggers"]
            rows = []
            for s in skills[:20]:
                triggers = ", ".join(s.get("triggers", []))[:25]
                rows.append([s.get("name", ""), s.get("description", "")[:45], triggers])
            print(f"\n{format_table(headers, rows, [16, 48, 20])}")

    elif c == "/model" and len(parts) > 1:
        from backend.agent.llm_client import LLMConfig
        new_model = parts[1]
        cfg._project["llm"] = cfg._project.get("llm", {})
        cfg._project["llm"]["model"] = new_model
        print(f"  {C.green('Model set to:')} {new_model}")
        print(f"  {C.dim('Note: Restart required to take effect.')}")

    elif c == "/workspace" and len(parts) > 1:
        new_ws = parts[1]
        cfg.project_root = Path(new_ws).resolve()
        print(f"  {C.green('Workspace set to:')} {cfg.project_root}")

    elif c == "/export" and len(parts) > 1:
        out_file = parts[1]
        state = graph._last_state if hasattr(graph, '_last_state') else None
        if state:
            export_data = {
                "session_id": state.session_id,
                "messages": [m.to_dict() for m in state.messages],
                "plan": [p.to_dict() for p in state.plan],
                "final_response": state.final_response,
            }
            Path(out_file).write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  {C.green('Exported to:')} {out_file}")
        else:
            print(f"  {C.yellow('No active session to export.')}")

    elif c == "/clear":
        await graph.cancel(sessions.active)
        sessions.create()
        print(f"  {C.green('Session cleared.')}")

    else:
        print(f"  {C.yellow('Unknown command:')} {c}. {C.dim('Type /help')}")


# ============================================================
# CLI Commands (argparse-based)
# ============================================================

def cmd_init(args):
    """Scaffold a new Aurora project."""
    name = args.name
    if not name:
        name = input(f"  {C.bold('Project name:')} ").strip()
        if not name:
            print(f"  {C.red('Project name is required.')}")
            return 1

    project_dir = Path(name).resolve()
    if project_dir.exists():
        print(f"  {C.red(f'Directory already exists: {project_dir}')}")
        return 1

    # Create directory structure
    dirs = [
        project_dir / "src",
        project_dir / "tests",
        project_dir / "skills",
        project_dir / "plugins",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # aurora.json
    aurora_config = {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "workspace": "./workspace",
        "max_context_tokens": 128000,
        "sandbox_mode": "danger-full-access",
        "approval_policy": "never",
        "language": "zh",
        "theme": "aurora-dark",
        "server": {"host": "127.0.0.1", "port": 9876},
        "agent": {"max_turn_iter": 30},
    }
    (project_dir / "aurora.json").write_text(
        json.dumps(aurora_config, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )

    # AGENTS.md
    agents_md = f"# {name}\n\n## Instructions\n\n- Default language: Chinese\n- Keep code clean and well-structured\n- Write tests for new features\n"
    (project_dir / "AGENTS.md").write_text(agents_md, encoding="utf-8")

    # README.md
    readme = f"# {name}\n\nAurora AI Coding Agent project.\n\n## Getting Started\n\n```bash\npip install -r requirements.txt\npython -m backend\n```\n"
    (project_dir / "README.md").write_text(readme, encoding="utf-8")

    # .gitignore
    gitignore = "__pycache__/\n*.pyc\n*.pyo\n.env\n.venv/\nnode_modules/\ndist/\n.aurora/\n*.egg-info/\n.pytest_cache/\n"
    (project_dir / ".gitignore").write_text(gitignore, encoding="utf-8")

    # src/main.py
    main_py = '"""Entry point for ' + name + '"""\nimport sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent.parent))\n\n\ndef main():\n    print("Hello from ' + name + '!")\n\n\nif __name__ == "__main__":\n    main()\n'
    (project_dir / "src" / "main.py").write_text(main_py, encoding="utf-8")

    # src/__init__.py (empty)
    (project_dir / "src" / "__init__.py").write_text("", encoding="utf-8")

    # tests/__init__.py (empty)
    (project_dir / "tests" / "__init__.py").write_text("", encoding="utf-8")

    print(f"\n  {C.green('✔ Project created:')} {project_dir}")
    print(f"  {C.dim('Structure:')}")
    for d in sorted(dirs):
        rel = d.relative_to(project_dir)
        print(f"    {C.cyan(str(rel) + '/')}")
    files_created = ["aurora.json", "AGENTS.md", "README.md", ".gitignore", "src/main.py", "src/__init__.py", "tests/__init__.py"]
    for f in files_created:
        print(f"    {C.dim(f)}")
    print(f"\n  {C.bold('Next steps:')}")
    print(f"    cd {name}")
    print(f"    {C.cyan('aurora serve')}  {C.dim('# Start the API server')}")
    return 0


def cmd_serve(args):
    """Start the Aurora API server."""
    import uvicorn
    from backend.config import init_config
    from backend.api import app

    cfg = init_config(".")
    host = cfg.get("server.host", "127.0.0.1")
    port = cfg.get("server.port", 9876)

    print(f"  {C.bold('Aurora AI Agent')} {C.dim(f'v{VERSION}')}")
    print(f"  {C.green(f'Server running at:')} http://{host}:{port}")
    print(f"  {C.dim(f'API docs:')} http://{host}:{port}/docs")
    print(f"  {C.dim('Press Ctrl+C to stop')}")
    print()
    uvicorn.run(app, host=host, port=port, log_level="info")


def cmd_desktop(args):
    """Launch the Electron desktop app."""
    desktop_dir = Path(__file__).parent.parent / "desktop"
    if not desktop_dir.exists():
        print(f"  {C.red(f'Desktop directory not found: {desktop_dir}')}")
        print(f"  {C.dim('Make sure the desktop/ folder exists with package.json')}")
        return 1

    print(f"  {C.bold('Launching Aurora Desktop...')}")
    print(f"  {C.dim(f'Directory: {desktop_dir}')}")

    # Check if node_modules exists
    if not (desktop_dir / "node_modules").exists():
        print(f"  {C.yellow('node_modules not found. Running npm install...')}")
        try:
            subprocess.run(["npm", "install"], cwd=str(desktop_dir), check=True)
        except subprocess.CalledProcessError:
            print(f"  {C.red('npm install failed. Please run it manually in desktop/')}")
            return 1

    # Launch electron
    try:
        subprocess.Popen(
            ["npx", "electron", "."],
            cwd=str(desktop_dir),
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  {C.green('Desktop app launched!')}")
    except Exception as e:
        print(f"  {C.red(f'Failed to launch: {e}')}")
        return 1

    return 0


# --- Interactive mode (no args) ---
async def interactive():
    setup_readline()
    show_banner()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from backend.config import init_config
    from backend.agent import LLMClient, LLMConfig, AgentGraph, MockLLMClient, Message

    cfg = init_config(".")
    use_mock = not cfg.llm_api_key

    if use_mock:
        print(f"  {C.yellow('[!] No API key set — using mock mode')}")
        print(f"  {C.dim('Set llm.api_key in aurora.json for real AI.')}\n")
        print("❌ 未配置 API Key"); sys.exit(1)
    else:
        provider = cfg.get("llm.provider", "openai")
        from backend.agent.llm_client import create_llm_client
        llm = create_llm_client(
            provider=provider, model=cfg.llm_model,
            api_key=cfg.llm_api_key, base_url=cfg.llm_base_url,
        )
        model_name = cfg.llm_model
        print(f"  {C.green('[OK] Connected to {provider}/{model_name}')}\n")

    from backend.tools import tool_registry

    async def tool_handler(name, args, ws):
        from backend.observability import tool_calls as tc_metric, tool_errors as te_metric
        tc_metric.inc()
        r = await tool_registry.execute(name, args, ws)
        if not r.success:
            te_metric.inc()
        return {"success": r.success, "output": r.output, "error": r.error}

    graph = AgentGraph(
        llm=llm, tool_handler=tool_handler,
        tools_schema=tool_registry.list_tools_openai(),
        max_turns=cfg.max_turn_iter,
    )

    sessions.create("CLI Session")

    while True:
        try:
            user_input = input(f"\n  {C.bold('You')} {C.dim('>')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {C.dim('Bye!')}")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/q"):
            print(f"  {C.dim('Exiting. Goodbye!')}")
            break

        if user_input.startswith("/"):
            await handle_command(user_input, cfg, tool_registry, graph)
            continue

        try:
            print(f"  {C.cyan('Aurora')} {C.dim('thinking...')}", end="\r", flush=True)
            state = await graph.run(user_input, session_id=sessions.active)
            sessions.record_msg(sessions.active, tokens=30)

            print(" " * 40, end="\r")

            response = state.final_response
            if len(response) > 2000:
                response = response[:2000] + f"\n{C.dim('... (truncated)')}"
            print(f"  {C.cyan('Aurora')} {C.dim('>')} {response}")

            if state.plan:
                plan_done = sum(1 for p in state.plan if p.status == "completed")
                plan_total = len(state.plan)
                print(f"\n  {C.bold(f'Plan: {plan_done}/{plan_total} steps')}")
                for p in state.plan:
                    icon = "✔" if p.status == "completed" else "✘" if p.status == "failed" else "▶" if p.status == "in_progress" else "○"
                    color = C.green if p.status == "completed" else C.red if p.status == "failed" else C.yellow
                    print(f"    {color(icon)} {p.description}")

        except Exception as e:
            print(f"\n  {C.red(f'Error: {e}')}")


# ============================================================
# Main entry point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        prog="aurora",
        description="Aurora AI Coding Agent CLI",
    )
    parser.add_argument("--version", action="version", version=f"Aurora v{VERSION}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # aurora init [name]
    init_parser = subparsers.add_parser("init", help="Scaffold a new Aurora project")
    init_parser.add_argument("name", nargs="?", default=None, help="Project name")

    # aurora serve
    subparsers.add_parser("serve", help="Start the Aurora API server")

    # aurora desktop
    subparsers.add_parser("desktop", help="Launch the Electron desktop app")

    args = parser.parse_args()

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "serve":
        return cmd_serve(args)
    elif args.command == "desktop":
        return cmd_desktop(args)
    elif args.command is None and len(sys.argv) > 1:
        # Unknown args: show help
        parser.print_help()
        return 1
    else:
        # No command: interactive mode
        asyncio.run(interactive())
        return 0


if __name__ == "__main__":
    sys.exit(main())
