#!/usr/bin/env bash
# Lance le CLI `sn` embarqué dans ce skill (code dans ./sncli, point d'entrée ./sn.py).
#
#   bash scripts/sn.sh <commande> [options]     ex. bash scripts/sn.sh get INC0012345 --format json
#   bash scripts/sn.sh setup [--with-deps]      prépare l'environnement et installe Chromium
#
# Rien n'est à installer à la main : au premier lancement, ce script prépare un
# environnement Python avec les dépendances du CLI, puis le réutilise.
#   * avec uv (recommandé) : `uv run --script sn.py`, environnement mis en cache par uv ;
#   * sinon avec python3 >= 3.10 : un venv dans ~/.local/state/sncli/skill-venv.
# Chromium (pour la connexion SSO de `sn login`) est téléchargé au premier
# `sn login` ou par `setup`. `--with-deps` installe aussi ses bibliothèques système
# (sudo), nécessaire sur une Ubuntu/WSL neuve.
#
# Variables : SNCLI_PYTHON (interpréteur à utiliser sans uv), SNCLI_NO_UV=1 (ignorer uv).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="${SNCLI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/sncli}"
BROWSER_MARK="$STATE/skill-browser-installed"

UV=""
if [ -z "${SNCLI_NO_UV:-}" ]; then
  UV="$(command -v uv || true)"
  [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
  [ -z "$UV" ] && [ -x "$HOME/.cargo/bin/uv" ] && UV="$HOME/.cargo/bin/uv"
fi

find_python() {
  local c
  for c in "${SNCLI_PYTHON:-}" python3 python3.13 python3.12 python3.11 python3.10 python; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

# Prépare (si besoin) le venv de repli et y installe les dépendances.
venv_python() {
  local venv="$STATE/skill-venv" req="$DIR/requirements.txt" py stamp
  stamp="$(cksum <"$req")"
  if [ ! -x "$venv/bin/python" ] || [ "$(cat "$venv/.requirements" 2>/dev/null)" != "$stamp" ]; then
    if ! py="$(find_python)"; then
      cat >&2 <<'EOF'
sn : il faut uv ou python3 >= 3.10 pour lancer le CLI. Le plus simple :
  curl -LsSf https://astral.sh/uv/install.sh | sh
EOF
      exit 127
    fi
    echo "sn : préparation de l'environnement Python (première exécution)..." >&2
    mkdir -p "$STATE"
    if ! "$py" -m venv "$venv" >&2; then
      echo "sn : impossible de créer le venv. Sous Ubuntu : sudo apt install python3-venv (ou installez uv)." >&2
      exit 127
    fi
    "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$req" >&2
    printf '%s' "$stamp" >"$venv/.requirements"
  fi
  printf '%s\n' "$venv/bin/python"
}

run() {
  if [ -n "$UV" ]; then
    "$UV" run --quiet --script "$DIR/sn.py" "$@"
  else
    "$(venv_python)" "$DIR/sn.py" "$@"
  fi
}

install_browser() {
  echo "sn : installation de Chromium pour la connexion SSO..." >&2
  run _install-browser "$@" >&2
  mkdir -p "$STATE" && touch "$BROWSER_MARK"
}

# Repère la commande `sn` (en sautant -P/--profile) pour installer Chromium avant `login`.
cmd=""
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  case "${args[$i]}" in
    -P|--profile) i=$((i + 2)) ;;
    -*) i=$((i + 1)) ;;
    *) cmd="${args[$i]}"; break ;;
  esac
done

if [ "$cmd" = "setup" ] && [ "${1:-}" = "setup" ]; then
  shift
  run --version
  install_browser "$@"
  echo "sn : prêt. Étapes suivantes (par l'utilisateur) : sn configure --instance <nom>, puis sn login." >&2
  exit 0
fi

# `sn login` passe par le navigateur sauf avec -m oauth|cookie ou --sdk-client.
login_needs_browser() {
  local a prev=""
  for a in "$@"; do
    case "$prev|$a" in
      -m\|oauth|-m\|cookie|--method\|oauth|--method\|cookie|*\|--method=oauth|*\|--method=cookie|*\|--sdk-client) return 1 ;;
    esac
    prev="$a"
  done
  # Méthode par défaut du profil : vérifiée par le CLI lui-même.
  run configure --show 2>/dev/null | grep -q '"auth_method": "browser"'
}

if [ "$cmd" = "login" ] && [ ! -e "$BROWSER_MARK" ] && [ -z "${SN_BROWSER_PATH:-}${SN_BROWSER_CHANNEL:-}" ] &&
   login_needs_browser "$@"; then
  install_browser || echo "sn : Chromium non installé ; 'sn login -m oauth' ou '-m cookie' restent possibles." >&2
fi

if [ -n "$UV" ]; then
  exec "$UV" run --quiet --script "$DIR/sn.py" "$@"
fi
exec "$(venv_python)" "$DIR/sn.py" "$@"
