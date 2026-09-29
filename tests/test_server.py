import sys

import pytest

from claude_buttons import server as mcp_server


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BUTTONS_HOME", str(tmp_path))


def test_tools_roundtrip():
    out = mcp_server.add_button("Say hi", "python", "print('hi')", "greets")
    assert out.startswith("Button created: [say-hi]")
    assert "say-hi" in mcp_server.list_buttons()
    assert "Exit code: 0" in mcp_server.run_button("say-hi") and "hi" in mcp_server.run_button("say-hi")
    assert "Button updated" in mcp_server.update_button("say-hi", color="#10b981")
    assert "#10b981" in mcp_server.get_button("say-hi")
    assert "deleted" in mcp_server.remove_button("say-hi")
    assert mcp_server.list_buttons() == "No buttons yet."


def test_tool_errors_are_messages_not_exceptions():
    assert mcp_server.add_button("x", "ruby", "p 1").startswith("Error:")
    assert mcp_server.get_button("missing").startswith("Error:")
    assert mcp_server.remove_button("missing").startswith("Error:")


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_dock_ignores_unknown_targets():
    from claude_buttons import dock

    assert dock.find_target_window(["no-such-app.exe"]) is None
    assert isinstance(dock.claude_theme_light(), bool)
