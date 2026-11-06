#!/usr/bin/env bash
# Installe la publication horaire de la veille sur une machine Linux (lab).
# Prérequis : git, python3 (3.10+), flock, et un accès en écriture au dépôt
# (clé de déploiement SSH avec « Allow write access », voir README).
#
#   git clone git@github.com:laaandryy/Veille-FI.git ~/veille-fi
#   bash ~/veille-fi/lab/install.sh            # toutes les heures à hh:07
#   MINUTE=23 bash ~/veille-fi/lab/install.sh  # autre minute
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
MINUTE="${MINUTE:-7}"
LOG="${LOG:-$HOME/.local/state/veille-fi.log}"
mkdir -p "$(dirname "$LOG")"

for bin in git python3 flock; do
  command -v "$bin" >/dev/null || { echo "Manquant : $bin" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Python 3.10+ requis" >&2; exit 1; }
git -C "$DIR" ls-remote -q origin >/dev/null || { echo "Le dépôt distant n'est pas joignable (clé SSH ?)" >&2; exit 1; }

LINE="$MINUTE * * * * cd $DIR && git pull -q --ff-only && flock -n /tmp/veille-fi.lock bash scripts/publish.sh >> $LOG 2>&1 # veille-fi"
( crontab -l 2>/dev/null | grep -v '# veille-fi$' || true; echo "$LINE" ) | crontab -
echo "Tâche installée :"
crontab -l | grep '# veille-fi$'
echo "Premier passage maintenant…"
cd "$DIR" && flock -n /tmp/veille-fi.lock bash scripts/publish.sh 2>&1 | tail -3
echo "Journal : $LOG"
