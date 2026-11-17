"""Copy the CLI sources (src/sncli) into the agent skill so that `gh skill install`
ships a working CLI. Run after any change under src/sncli:

    uv run python tools/sync_skill_cli.py

tests/test_skill.py fails while the copy is out of date.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "sncli"
DEST = ROOT / "skills" / "servicenow-itsm" / "scripts" / "sncli"


def source_files(base: Path) -> dict[str, bytes]:
    return {p.relative_to(base).as_posix(): p.read_bytes()
            for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts}


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    for rel, data in source_files(SRC).items():
        out = DEST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    print(f"Copied {len(source_files(SRC))} files to {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
