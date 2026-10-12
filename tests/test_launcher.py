from pathlib import Path

from aion_target.launcher import desktop_entry


def test_desktop_entry_runs_venv_python_in_project_dir():
    text = desktop_entry("/home/joueur/aion-target/.venv/bin/python",
                         Path("/home/joueur/aion-target"), Path("/x/icon.png"))
    assert 'Exec="/home/joueur/aion-target/.venv/bin/python" -m aion_target' in text
    assert "Path=/home/joueur/aion-target" in text
    assert "Terminal=false" in text
    assert text.startswith("[Desktop Entry]")
