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
- **Prix en direct** : dans le navigateur, les prix crypto sont rafraîchis toutes les 90 s (CoinGecko).
  Les autres cours (indices, devises, matières premières) datent du passage de 05:00 UTC.

## Fonctionnement

1. Chaque jour à 05:00 UTC, `scripts/veille.py` lit `actifs.csv` et `scripts/sources.json`.
2. Il interroge Yahoo Finance (cours), CoinGecko (crypto), DefiLlama (stablecoins, TVL),
   alternative.me (Fear & Greed) et Google News (actualités).
3. Il génère `index.html` et `data/veille.json` puis les pousse sur `main`.
   Si une source est indisponible, les données du passage précédent sont conservées.

Lancement manuel : onglet **Actions** → **veille** → **Run workflow**.
Modifier `actifs.csv` ou `scripts/` sur `main` relance aussi la génération.

## Ajouter un actif

Ajoutez une ligne `Nom;Symbole Yahoo;Catégorie;Identifiant CoinGecko` dans `actifs.csv`.
Le symbole est celui de [Yahoo Finance](https://finance.yahoo.com) (ex. `^FCHI`, `AAPL`, `MC.PA`, `BTC-USD`, `EURUSD=X`, `GC=F`).
L'identifiant CoinGecko (facultatif, ex. `bitcoin`) active le prix en direct pour une crypto cotée en USD.

## Actualités

Les thèmes (requêtes FR / EN et termes devant figurer dans le titre) et les filtres globaux
(titres exclus, médias exclus, mots exclus de la recherche) sont dans `scripts/sources.json`.

## En local

```bash
python3 scripts/veille.py   # Python 3.10+, aucune dépendance
```

Informations fournies à titre indicatif, sans valeur de conseil en investissement.
