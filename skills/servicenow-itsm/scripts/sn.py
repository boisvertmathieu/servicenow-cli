# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx>=0.27",
#   "click>=8.1",
#   "rich>=13",
#   "playwright>=1.45",
#   "keyring>=24",
# ]
# ///
"""Point d'entrée du CLI `sn` embarqué dans le skill (copie de src/sncli).

Lancez-le avec `bash scripts/sn.sh ...`, qui prépare l'environnement Python.
Argument réservé : `_install-browser [--with-deps]` installe Chromium pour `sn login`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if len(sys.argv) > 1 and sys.argv[1] == "_install-browser":
    from playwright.__main__ import main as playwright_main

    sys.argv = ["playwright", "install", *sys.argv[2:], "chromium"]
    playwright_main()
else:
    from sncli.cli import main

    main(prog_name="sn")
