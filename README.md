# Veille finance & crypto

Site statique (`index.html`) régénéré chaque jour par la tâche planifiée **veille**
(GitHub Actions, `.github/workflows/veille.yml`), avec la même identité visuelle que la veille
technologique. Aucune clé d'API n'est nécessaire.

## Contenu

- **Bandeau de cotations** : tous les actifs de `actifs.csv`, avec la variation du jour. Un clic ouvre leur graphe.
- **Résumé du jour** : capitalisation crypto, dominance BTC, Fear & Greed, stablecoins, TVL DeFi,
  plus fortes hausses et baisses, décrochages de stablecoins et nouvelles actualités depuis hier.
- **Marchés** : graphes CAC 40 et S&P 500, explorateur de graphe pour chaque actif
  (5 jours en 30 min, puis 1 mois → 5 ans en journalier, échelle log), tableau des performances
  (1J, 1S, 1M, YTD, 1A, 5A) triable et filtrable.
- **Crypto** : top 20 (hors stablecoins et jetons « wrapped »), indice Fear & Greed, TVL DeFi, dominance.
- **Stablecoins** : capitalisation totale et historique, tableau (prix, écart à l'ancrage, part, variations),
  historique de l'ancrage de chaque stablecoin avec seuils ±0,5 %.
- **Actualités** : Google News FR + EN par thème, filtrées contre publicités, promotions, prédictions
  de cours et communiqués ; badge « Nouveau » pour ce qui n'avait pas été vu la veille.
- Recherche dans chaque onglet, mode clair / sombre, affichage mobile.
- **Rafraîchissement** : la page se recharge toute seule chaque minute (onglet et position conservés) et
  les prix crypto sont repris en direct de CoinGecko à chaque chargement.
  Les autres cours (indices, devises, matières premières) datent du dernier passage de la tâche.

## Fonctionnement

1. `scripts/publish.sh` relit la version publiée (pour l'historique des nouveautés), puis lance
   `scripts/veille.py`, qui lit `actifs.csv` et `scripts/sources.json`.
2. Il interroge CNBC (indices, devises, matières premières), Binance puis CoinGecko (historique crypto),
   la BCE (secours devises), CoinGecko (marché crypto), DefiLlama (stablecoins, TVL),
   alternative.me (Fear & Greed) et Google News (actualités). Yahoo Finance sert de dernier recours.
   Si une source est indisponible, les données du passage précédent sont conservées.
3. Il génère le site dans `_site/` (non versionné) et le publie sur la branche **`gh-pages`**,
   en un seul commit remplacé à chaque passage : le dépôt ne grossit pas.

GitHub Pages sert la branche `gh-pages` (Settings → Pages → Branch : `gh-pages` / root).

### Publication horaire depuis le lab

1. Sur le lab, créez une clé SSH dédiée : `ssh-keygen -t ed25519 -f ~/.ssh/veille-fi -N ""`.
2. Ajoutez `~/.ssh/veille-fi.pub` au dépôt : Settings → Deploy keys → Add deploy key,
   en cochant **Allow write access**.
3. Clonez avec cette clé et installez la tâche :

   ```bash
   GIT_SSH_COMMAND="ssh -i ~/.ssh/veille-fi -o IdentitiesOnly=yes" \
     git clone git@github.com:laaandryy/Veille-FI.git ~/veille-fi
   git -C ~/veille-fi config core.sshCommand "ssh -i ~/.ssh/veille-fi -o IdentitiesOnly=yes"
   bash ~/veille-fi/lab/install.sh
   ```

   `install.sh` vérifie les prérequis (git, Python 3.10+, flock), ajoute une ligne crontab
   (toutes les heures à hh:07, `MINUTE=23` pour changer), lance un premier passage et écrit le journal
   dans `~/.local/state/veille-fi.log`. Chaque passage récupère d'abord les modifications de `main`.

### Secours GitHub Actions

Le workflow `.github/workflows/veille.yml` publie aussi chaque jour vers 04:17 et 06:47 UTC, et à chaque
modification de `actifs.csv` ou `scripts/` sur `main`. Lancement manuel : **Actions** → **veille** → **Run workflow**.
Sur une PR, il génère le site à blanc et le joint en artefact.

## Ajouter un actif

Ajoutez une ligne `Nom;Catégorie;Symbole;CNBC;CoinGecko` dans `actifs.csv` :

- **Symbole** : identifiant de l'actif au format Yahoo Finance (ex. `^FCHI`, `MC.PA`, `BTC-USD`, `EURUSD=X`, `GC=F`).
  Pour une crypto, `XXX-USD` suffit : l'historique vient de Binance (paire `XXXUSDT`).
- **CNBC** : symbole utilisé sur [cnbc.com/quotes](https://www.cnbc.com/quotes/.FCHI) pour les indices, actions,
  devises et matières premières (ex. `.FCHI`, `.SPX`, `EUR=`, `@GC.1`, `@LCO.1`, `MC-FR`).
- **CoinGecko** (facultatif, ex. `bitcoin`) : active le prix en direct dans la page et sert de secours.

## Actualités

Les thèmes (requêtes FR / EN et termes devant figurer dans le titre) et les filtres globaux
(titres exclus, médias exclus, mots exclus de la recherche) sont dans `scripts/sources.json`.

## En local

```bash
python3 scripts/veille.py   # Python 3.10+, aucune dépendance ; résultat dans _site/
```

Informations fournies à titre indicatif, sans valeur de conseil en investissement.
