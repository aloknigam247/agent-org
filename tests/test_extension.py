import subprocess
from pathlib import Path


def test_extension_runtime_contract():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_suffix(".mjs"))],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
