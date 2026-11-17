"""The agent skill (skills/servicenow-itsm) ships its own copy of the CLI."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")  # Python 3.11+

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "servicenow-itsm"
SCRIPTS = SKILL / "scripts"

sys.path.insert(0, str(ROOT / "tools"))
from sync_skill_cli import SRC, source_files  # noqa: E402


def test_bundled_cli_matches_src():
    bundled = source_files(SCRIPTS / "sncli")
    expected = source_files(SRC)
    assert bundled.keys() == expected.keys() and bundled == expected, (
        "skills/servicenow-itsm/scripts/sncli is out of date: run `uv run python tools/sync_skill_cli.py`")


def _norm(req: str) -> str:
    return req.replace(" ", "").lower()


def test_bundled_dependencies_match_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    expected = {_norm(d) for d in project["dependencies"]}
    for extra in ("browser", "keyring"):
        expected |= {_norm(d) for d in project["optional-dependencies"][extra]}

    block = re.search(r"^# /// script\n(.*?)^# ///$", (SCRIPTS / "sn.py").read_text(), re.S | re.M).group(1)
    inline = tomllib.loads("\n".join(line[2:] for line in block.splitlines()))["dependencies"]
    assert {_norm(d) for d in inline} == expected

    reqs = [line.strip() for line in (SCRIPTS / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")]
    assert {_norm(d) for d in reqs} == expected


def test_skill_frontmatter_is_spec_compliant():
    text = (SKILL / "SKILL.md").read_text()
    fm = text.split("---", 2)[1]
    name = re.search(r"^name:\s*(\S+)$", fm, re.M).group(1)
    assert name == SKILL.name and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)
    desc_block = fm.split("description:", 1)[1]
    desc = " ".join(line.strip() for line in desc_block.splitlines()[1:] if line.strip())
    assert 0 < len(desc) <= 1024


@pytest.mark.skipif(not shutil.which("uv") or not shutil.which("bash"), reason="needs uv and bash")
def test_bundled_launcher_against_fake_instance(fake, browser_session):
    def sn(*args):
        return subprocess.run(["bash", str(SCRIPTS / "sn.sh"), *args], capture_output=True, text=True, timeout=300)

    assert sn("configure", "--instance", fake.url).returncode == 0
    cookie = "; ".join(f"{c.name}={c.value}" for c in browser_session.cookies.jar)
    res = sn("login", "--method", "cookie", "--cookie", cookie)
    assert res.returncode == 0, res.stderr

    res = sn("get", "INC0010002", "-f", "number,short_description", "--format", "json")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["short_description"] == "Printer jam floor 3"

    res = sn("search", "vpn", "-t", "incident", "--format", "json")
    assert {r["number"] for r in json.loads(res.stdout)[0]["rows"]} == {"INC0010001", "INC0010003"}
