"""The iPhone app's team list (host console > Choose the teams yourself) is generated from teams.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_swift_team_presets_match_teams_py():
    result = subprocess.run([sys.executable, str(ROOT / "ios" / "scripts" / "make_team_presets.py"), "--check"])
    assert result.returncode == 0, "Run: python3 ios/scripts/make_team_presets.py"
