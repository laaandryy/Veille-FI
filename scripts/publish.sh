#!/usr/bin/env bash
# Génère la veille et la publie sur la branche gh-pages (GitHub Pages).
#
# La branche gh-pages ne contient qu'un seul commit, remplacé à chaque passage :
# le dépôt ne grossit pas, même avec une publication toutes les heures.
# L'historique des « nouveautés » est relu depuis la version publiée, si bien que
# le lab et GitHub Actions peuvent publier tour à tour sans perdre le fil.
#
# Utilisation : bash scripts/publish.sh
# Variables facultatives : PAGES_REMOTE (origin), PAGES_BRANCH (gh-pages), VEILLE_OUT (_site)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REMOTE="${PAGES_REMOTE:-origin}"
BRANCH="${PAGES_BRANCH:-gh-pages}"
export VEILLE_OUT="${VEILLE_OUT:-$ROOT/_site}"
mkdir -p "$VEILLE_OUT/data"

# 1. Reprendre l'état publié (historique des liens déjà vus).
if git fetch -q "$REMOTE" "$BRANCH" 2>/dev/null; then
  git show FETCH_HEAD:data/veille.json > "$VEILLE_OUT/data/veille.json.tmp" 2>/dev/null \
    && mv "$VEILLE_OUT/data/veille.json.tmp" "$VEILLE_OUT/data/veille.json" \
    || rm -f "$VEILLE_OUT/data/veille.json.tmp"
fi

# 2. Collecter et générer (s'arrête ici si aucune donnée n'a pu être collectée).
python3 scripts/veille.py

# 3. Préparer le site et le publier en un commit unique, sans toucher à la copie de travail.
site="$(mktemp -d)"
trap 'rm -rf "$site" "$site.index"' EXIT
cp -r "$VEILLE_OUT/." "$site/"
cp actifs.csv "$site/"
[ -f CNAME ] && cp CNAME "$site/"
touch "$site/.nojekyll"

export GIT_INDEX_FILE="$site.index"
git --work-tree="$site" add -A
tree="$(git write-tree)"
unset GIT_INDEX_FILE
export GIT_AUTHOR_NAME="veille-bot" GIT_COMMITTER_NAME="veille-bot"
export GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
commit="$(git commit-tree "$tree" -m "veille: $(date -u +'%Y-%m-%d %H:%M') UTC")"
git push -qf "$REMOTE" "$commit:refs/heads/$BRANCH"
echo "Publié sur $BRANCH : $commit"
