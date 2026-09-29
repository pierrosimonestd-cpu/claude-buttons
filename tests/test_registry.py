import time

import pytest

from claude_buttons import registry


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BUTTONS_HOME", str(tmp_path))
    return tmp_path


def test_add_and_load():
    b = registry.add("Hello World", "python", "print('hi')", "says hi")
    assert b["id"] == "hello-world"
    assert registry.load() == [b]
    assert registry.read_script("hello-world") == "print('hi')"


def test_unique_ids():
    a = registry.add("Test", "batch", "echo 1")
    b = registry.add("Test", "batch", "echo 2")
    assert (a["id"], b["id"]) == ("test", "test-2")


def test_accented_name_slug():
    assert registry.add("Città è però", "python", "pass")["id"] == "citta-e-pero"


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(name="", language="python", script="pass"),
        dict(name="x", language="ruby", script="pass"),
        dict(name="x", language="python", script="  "),
        dict(name="x", language="python", script="pass", color="red"),
        dict(name="x", language="python", script="pass", cwd="Z:/does/not/exist"),
    ],
)
def test_validation(kwargs):
    with pytest.raises(registry.RegistryError):
        registry.add(**kwargs)
    assert registry.load() == []


def test_update_changes_language_and_moves_script(tmp_home):
    registry.add("X", "python", "print(1)")
    b = registry.update("x", language="powershell", script="Write-Output 1", color="#ff0000")
    assert b["script"] == "x.ps1" and b["color"] == "#ff0000"
    assert not (tmp_home / "scripts" / "x.py").exists()
    assert registry.read_script("x") == "Write-Output 1"


def test_update_is_atomic_on_validation_error():
    registry.add("X", "python", "print(1)")
    with pytest.raises(registry.RegistryError):
        registry.update("x", name="Renamed", color="not-a-color")
    assert registry.get("x")["name"] == "X"


def test_remove(tmp_home):
    registry.add("X", "python", "print(1)")
    registry.run("x")
    registry.remove("x")
    assert registry.load() == []
    assert list((tmp_home / "scripts").iterdir()) == []
    assert registry.last_log("x") is None
    with pytest.raises(registry.RegistryError):
        registry.remove("x")


@pytest.mark.parametrize(
    "language,script",
    [
        ("python", "print('perché ok')"),
        ("powershell", "Write-Output 'perché ok'"),
        ("batch", "@echo off\necho ok"),
    ],
)
def test_run_each_language(language, script):
    registry.add("R", language, script)
    code, out = registry.run("r", timeout=60)
    assert code == 0
    assert "ok" in out
    assert "exit 0" in registry.last_log("r")


def test_run_failure_and_cwd(tmp_path):
    registry.add("F", "python", "import os, sys; print(os.getcwd()); sys.exit(3)", cwd=str(tmp_path))
    code, out = registry.run("f")
    assert code == 3
    assert str(tmp_path) in out


def test_last_status():
    registry.add("A", "python", "pass")
    registry.add("B", "python", "raise SystemExit(1)")
    assert registry.last_status("a") is None
    registry.run("a")
    registry.run("b")
    assert (registry.last_status("a"), registry.last_status("b")) == ("ok", "err")


def test_run_timeout():
    registry.add("T", "python", "import time; time.sleep(10)")
    code, out = registry.run("t", timeout=1)
    assert code == -1 and "stopped after" in out


def test_background_child_does_not_block():
    # a script that launches a long-lived program must return as soon as it exits
    registry.add(
        "Launcher",
        "python",
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'])\n"
        "print('launched')",
    )
    start = time.monotonic()
    code, out = registry.run("launcher", timeout=30)
    assert code == 0 and "launched" in out
    assert time.monotonic() - start < 4
