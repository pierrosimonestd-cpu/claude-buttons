"""Button registry: the only module that knows the on-disk data format.

Layout (default ~/.claude-buttons, override with CLAUDE_BUTTONS_HOME):
    buttons.json   list of buttons
    scripts/       the scripts buttons run
    logs/          output of each button's last run
"""

from __future__ import annotations

import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

LANGUAGES = {
    "powershell": ".ps1",
    "python": ".py",
    "batch": ".bat",
}
DEFAULT_COLOR = "#3b82f6"
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOG_EXIT_RE = re.compile(r" — exit (-?\d+) — ")


class RegistryError(ValueError):
    """Validation error or unknown button."""


# --- paths --------------------------------------------------------------------


def home() -> Path:
    base = os.environ.get("CLAUDE_BUTTONS_HOME")
    path = Path(base) if base else Path.home() / ".claude-buttons"
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    return path


def buttons_file() -> Path:
    return home() / "buttons.json"


def script_path(button: dict) -> Path:
    return home() / "scripts" / button["script"]


def log_path(button_id: str) -> Path:
    return home() / "logs" / f"{button_id}.log"


# --- read / write ---------------------------------------------------------------


def load() -> list[dict]:
    path = buttons_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data.get("buttons", []) if isinstance(data, dict) else []


def _save(buttons: list[dict]) -> None:
    _atomic_write(buttons_file(), json.dumps({"buttons": buttons}, indent=2, ensure_ascii=False), "utf-8")


def _atomic_write(path: Path, text: str, encoding: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_script(filename: str, language: str, content: str) -> None:
    # Windows PowerShell 5.1 only reads non-ASCII characters correctly with a BOM
    encoding = "utf-8-sig" if language == "powershell" else "utf-8"
    if language == "batch":
        content = content.replace("\r\n", "\n").replace("\n", "\r\n")
    _atomic_write(home() / "scripts" / filename, content, encoding)


def get(button_id: str) -> dict:
    for b in load():
        if b["id"] == button_id:
            return b
    raise RegistryError(f"No button with id '{button_id}'")


# --- validation -----------------------------------------------------------------


def _slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "button"


def _check_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise RegistryError("Button name cannot be empty")
    return name


def _check_script(script: str) -> None:
    if not script.strip():
        raise RegistryError("Script cannot be empty")


def _check_language(language: str) -> None:
    if language not in LANGUAGES:
        raise RegistryError(f"Unsupported language '{language}'. Use one of: {', '.join(LANGUAGES)}")


def _check_color(color: str) -> None:
    if not _COLOR_RE.match(color):
        raise RegistryError(f"Invalid color '{color}': use the #rrggbb format")


def _check_cwd(cwd: str | None) -> None:
    if cwd and not Path(cwd).is_dir():
        raise RegistryError(f"Working directory '{cwd}' does not exist")


# --- operations -----------------------------------------------------------------


def add(
    name: str,
    language: str,
    script: str,
    description: str = "",
    cwd: str | None = None,
    color: str = DEFAULT_COLOR,
) -> dict:
    name = _check_name(name)
    _check_script(script)
    _check_language(language)
    _check_color(color)
    _check_cwd(cwd)

    buttons = load()
    ids = {b["id"] for b in buttons}
    base = _slugify(name)
    button_id, n = base, 2
    while button_id in ids:
        button_id, n = f"{base}-{n}", n + 1

    button = {
        "id": button_id,
        "name": name,
        "description": description,
        "language": language,
        "script": button_id + LANGUAGES[language],
        "cwd": cwd,
        "color": color,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _write_script(button["script"], language, script)
    buttons.append(button)
    _save(buttons)
    return button


def update(
    button_id: str,
    name: str | None = None,
    description: str | None = None,
    language: str | None = None,
    script: str | None = None,
    cwd: str | None = None,
    color: str | None = None,
) -> dict:
    buttons = load()
    for button in buttons:
        if button["id"] == button_id:
            break
    else:
        raise RegistryError(f"No button with id '{button_id}'")

    # validate everything before touching anything
    if name is not None:
        name = _check_name(name)
    if color is not None:
        _check_color(color)
    if cwd is not None:
        _check_cwd(cwd)
    if language is not None:
        _check_language(language)
    if script is not None:
        _check_script(script)

    if name is not None:
        button["name"] = name
    if description is not None:
        button["description"] = description
    if color is not None:
        button["color"] = color
    if cwd is not None:
        button["cwd"] = cwd or None  # empty string clears it

    old_script = script_path(button)
    if language is not None and language != button["language"]:
        if script is None:
            script = old_script.read_text(encoding="utf-8-sig")
        button["language"] = language
        button["script"] = button_id + LANGUAGES[language]
    if script is not None:
        _write_script(button["script"], button["language"], script)
        if old_script != script_path(button):
            old_script.unlink(missing_ok=True)

    _save(buttons)
    return button


def remove(button_id: str) -> None:
    buttons = load()
    kept = [b for b in buttons if b["id"] != button_id]
    if len(kept) == len(buttons):
        raise RegistryError(f"No button with id '{button_id}'")
    removed = next(b for b in buttons if b["id"] == button_id)
    _save(kept)
    script_path(removed).unlink(missing_ok=True)
    log_path(button_id).unlink(missing_ok=True)


def read_script(button_id: str) -> str:
    return script_path(get(button_id)).read_text(encoding="utf-8-sig")


# --- execution ------------------------------------------------------------------


def _python_exe() -> str:
    exe = Path(sys.executable)
    # under pythonw.exe (the bar) use the python.exe next to it
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return shutil.which("python") or str(exe)


def command_for(button: dict) -> list[str]:
    path = str(script_path(button))
    lang = button["language"]
    if lang == "powershell":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    if lang == "python":
        return [_python_exe(), "-X", "utf8", path]
    if lang == "batch":
        return ["cmd", "/c", path]
    raise RegistryError(f"Unsupported language '{lang}'")


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode(locale.getpreferredencoding(False), errors="replace")
    # \r\n would become \r\r\n when the log is written in text mode
    return text.replace("\r\n", "\n")


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        proc.kill()
    proc.wait()


def run(button_id: str, timeout: float | None = None) -> tuple[int, str]:
    """Run a button, save its log and return (exit_code, output)."""
    button = get(button_id)
    started = datetime.now()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # Output goes to a file, not a pipe: a background program started by the script
    # would inherit the pipe and keep the run "busy" until it exits.
    with tempfile.TemporaryFile() as out:
        try:
            proc = subprocess.Popen(
                command_for(button),
                cwd=button.get("cwd") or None,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
            )
            try:
                code, suffix = proc.wait(timeout=timeout), ""
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                code, suffix = -1, f"\n[stopped after {timeout}s]"
            out.seek(0)
            output = _decode(out.read()) + suffix
        except OSError as e:
            code, output = -1, f"[could not start the script: {e}]"

    elapsed = (datetime.now() - started).total_seconds()
    header = f"# {button['name']} — {started:%Y-%m-%d %H:%M:%S} — exit {code} — {elapsed:.1f}s\n\n"
    log_path(button_id).write_text(header + output, encoding="utf-8")
    return code, output


def last_log(button_id: str) -> str | None:
    path = log_path(button_id)
    return path.read_text(encoding="utf-8") if path.exists() else None


def last_status(button_id: str) -> str | None:
    """'ok' / 'err' based on the last run, None if never run."""
    path = log_path(button_id)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        match = _LOG_EXIT_RE.search(f.readline())
    if not match:
        return None
    return "ok" if match.group(1) == "0" else "err"
