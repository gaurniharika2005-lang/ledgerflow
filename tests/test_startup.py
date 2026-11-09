import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def import_security_with_key(key):
    # A fresh Python process, so we see what a real startup would do.
    env = {**os.environ, "JWT_SECRET_KEY": key}
    return subprocess.run(
        [sys.executable, "-c", "import app.security"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_app_refuses_to_start_with_a_short_jwt_key():
    result = import_security_with_key("too-short")
    assert result.returncode != 0
    assert "at least 32 characters" in result.stderr


def test_app_starts_with_a_long_enough_jwt_key():
    result = import_security_with_key("x" * 40)
    assert result.returncode == 0, result.stderr