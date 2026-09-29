"""MCP server that lets Claude create and manage the buttons on the bar."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # mcp 2.x renamed FastMCP to MCPServer; the API we use is the same
    from mcp.server.mcpserver import MCPServer  # noqa: E402
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer  # noqa: E402

import registry  # noqa: E402

ROOT = Path(__file__).resolve().parent


def ensure_bar_running() -> None:
    """Start the bar in the background unless it is already running."""
    if sys.platform != "win32" or os.environ.get("CLAUDE_BUTTONS_NO_AUTOSTART"):
        return
    import dock
    if dock.is_running():
        return
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    cmd = [str(pythonw if pythonw.exists() else exe), str(ROOT / "bar.pyw")]
    io = dict(cwd=ROOT, close_fds=True, stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The MCP client closes our Job Object on exit, killing every child process.
    # The bar must outlive the session, so it has to be started outside the Job.
    detached = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, creationflags=detached | subprocess.CREATE_BREAKAWAY_FROM_JOB, **io)
    except OSError:
        # Job without BREAKAWAY_OK: let WMI create it, since WMI is not part of our Job
        line = subprocess.list2cmdline(cmd).replace("'", "''")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             f"Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{{CommandLine='{line}'; "
             f"CurrentDirectory='{ROOT}'}} | Out-Null"],
            creationflags=subprocess.CREATE_NO_WINDOW, **io).wait(timeout=15)


mcp = MCPServer(
    "claude-buttons",
    instructions=(
        "Manages the user's 'Claude Buttons': a bar docked to the right edge of the Claude desktop "
        "window (the user opens/closes it by clicking the tab on the edge or with Ctrl+Alt+B). "
        "Each button runs a script (powershell, python or batch) when the user clicks it. Use "
        "add_button to create handy workflows or scripts, then run_button to test them. Scripts run "
        "without a console and without interactive input: no Read-Host/input(), put parameters in "
        "the script itself. The user can see the output (stdout+stderr) from the button's ⋯ menu > "
        "Show output."
    ),
)


def _describe(b: dict) -> str:
    extra = f", cwd={b['cwd']}" if b.get("cwd") else ""
    desc = f" — {b['description']}" if b.get("description") else ""
    return f"[{b['id']}] {b['name']} ({b['language']}{extra}){desc}"


@mcp.tool()
def add_button(name: str, language: str, script: str, description: str = "",
               cwd: str | None = None, color: str = registry.DEFAULT_COLOR) -> str:
    """Add a button to the bar. It shows up immediately.

    Args:
        name: short label shown on the button (e.g. "Clean Downloads").
        language: "powershell", "python" or "batch".
        script: full script content. Must be non-interactive.
        description: what the button does, shown under the name and as a tooltip.
        cwd: absolute working directory to run the script in (optional).
        color: button color as #rrggbb.
    """
    try:
        b = registry.add(name, language, script, description, cwd, color)
    except registry.RegistryError as e:
        return f"Error: {e}"
    return f"Button created: {_describe(b)}\nScript: {registry.script_path(b)}"


@mcp.tool()
def list_buttons() -> str:
    """List every button with its id, language and description."""
    buttons = registry.load()
    if not buttons:
        return "No buttons yet."
    return "\n".join(_describe(b) for b in buttons)


@mcp.tool()
def get_button(button_id: str) -> str:
    """Show a button's details and script content."""
    try:
        b = registry.get(button_id)
        script = registry.read_script(button_id)
    except (registry.RegistryError, OSError) as e:
        return f"Error: {e}"
    return f"{_describe(b)}\nColor: {b['color']}\nFile: {registry.script_path(b)}\n\n{script}"


@mcp.tool()
def update_button(button_id: str, name: str | None = None, description: str | None = None,
                  language: str | None = None, script: str | None = None,
                  cwd: str | None = None, color: str | None = None) -> str:
    """Change an existing button. Pass only the fields to change (cwd="" clears it)."""
    try:
        b = registry.update(button_id, name, description, language, script, cwd, color)
    except (registry.RegistryError, OSError) as e:
        return f"Error: {e}"
    return f"Button updated: {_describe(b)}"


@mcp.tool()
def remove_button(button_id: str) -> str:
    """Delete a button and its script."""
    try:
        registry.remove(button_id)
    except registry.RegistryError as e:
        return f"Error: {e}"
    return f"Button '{button_id}' deleted."


@mcp.tool()
def run_button(button_id: str, timeout_seconds: float = 120) -> str:
    """Run a button to test it and return its exit code and output."""
    try:
        code, output = registry.run(button_id, timeout=timeout_seconds)
    except registry.RegistryError as e:
        return f"Error: {e}"
    output = output if len(output) <= 20000 else output[:20000] + "\n[output truncated]"
    return f"Exit code: {code}\n\n{output}"


if __name__ == "__main__":
    try:
        ensure_bar_running()
    except Exception:  # the tools must work even if the bar cannot start
        pass
    mcp.run()
