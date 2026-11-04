#!/usr/bin/env python3
"""Veille finance & crypto quotidienne.

Collecte, sans clé d'API :
  - les cours et l'historique (5 ans en journalier, 5 jours en 30 min) des actifs
    de actifs.csv (Yahoo Finance) ;
  - le marché crypto : top 20, capitalisation globale, dominance (CoinGecko),
    indice Fear & Greed (alternative.me), TVL DeFi (DefiLlama) ;
  - les stablecoins : capitalisations, ancrage, historique (DefiLlama) ;
  - les actualités par thème (Google News FR + EN, filtrées contre pubs et promotions),
puis génère index.html (site statique, données embarquées) et data/veille.json
(instantané + historique des liens déjà vus pour repérer les nouveautés).

Bibliothèque standard Python uniquement : aucune dépendance à installer.
En cas d'échec d'une source, les données du passage précédent sont conservées.
"""
from __future__ import annotations

import csv
import html
import http.cookiejar
import json
import math
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "actifs.csv"
SOURCES_PATH = ROOT / "scripts" / "sources.json"
TEMPLATE_PATH = ROOT / "scripts" / "template.html"
DATA_PATH = ROOT / "data" / "veille.json"
OUT_PATH = ROOT / "index.html"

NOW = datetime.now(timezone.utc)
NEWS_WINDOW_DAYS = 7        # actualités plus anciennes ignorées
MAX_NEWS = 8                # actualités affichées par thème
HISTORY_DAYS = 60           # conservation des liens « déjà vus »
TOP_CRYPTO = 20
TOP_STABLES = 12
DEPEG_ALERT = 0.005
# Jetons écartés du top crypto : stablecoins, jetons « wrapped » / de staking, fonds tokenisés.
NOT_COINS = {"usdt", "usdc", "usde", "dai", "usds", "fdusd", "pyusd", "usd1", "usdg", "rlusd", "usdf", "usdtb", "bsc-usd",
             "steth", "wsteth", "weeth", "reth", "cbeth", "meth", "jitosol", "bnsol", "wbtc", "weth", "cbbtc", "lbtc",
             "solvbtc", "susde", "susds", "syrupusdc", "buidl", "usyc", "ustb", "ousg", "figr_heloc"}
NOT_STABLES = {"BUIDL", "USYC", "USTB", "OUSG", "BENJI"}  # fonds monétaires tokenisés         # écart à l'ancrage signalé au-delà de 0,5 %
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA = "veille-fi-bot/1.0 (+https://github.com/laaandryy/Veille-FI)"
TIMEOUT = 15


# --------------------------------------------------------------------------- utils
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def fetch(url: str, headers: dict | None = None, ua: str = UA, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*", **(headers or {})})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001
            last = e
            code = getattr(e, "code", None)
            if code in (400, 401, 403, 404):
                break
            time.sleep(4 * (attempt + 1) * (3 if code == 429 else 1))
    raise last  # type: ignore[misc]


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw))


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def iso(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def clean_text(s: str | None, limit: int = 220) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


def rnd(v: float | None, ref: float | None = None) -> float | None:
    """Arrondi à ~6 chiffres significatifs (suffisant pour les graphes, JSON léger)."""
    if v is None:
        return None
    ref = abs(ref if ref is not None else v) or 1
    return round(v, max(0, 5 - math.floor(math.log10(ref))))


def pct(a: float | None, b: float | None) -> float | None:
    return round((a / b - 1) * 100, 3) if a is not None and b not in (None, 0) else None


# --------------------------------------------------------------------------- inputs
def read_assets() -> list[dict]:
    """actifs.csv : Nom;Catégorie;Symbole;CNBC;CoinGecko (voir README)."""
    raw = CSV_PATH.read_bytes().decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(raw.splitlines(), delimiter=";"))
    out = []
    for row in rows[1:]:
        row = [c.strip() for c in row] + ["", "", "", "", ""]
        if not row[0] or not row[2]:
            continue
        out.append({"name": row[0], "category": row[1] or "Autres", "symbol": row[2],
                    "cnbc": row[3] or None, "gecko": row[4] or None})
    return out


# --------------------------------------------------------------------------- marchés
# Chaque fournisseur renvoie {"currency", "daily": [(jour, clôture)], "price", "intraday": [(ts, cours)]}.
# Ordre : CNBC (indices, devises, matières premières), Binance puis CoinGecko (crypto), BCE (devises),
# et Yahoo Finance en dernier recours (souvent bloqué depuis GitHub Actions).
_YH: dict = {}


def yahoo_opener():
    if "op" in _YH:
        return _YH["op"], _YH["crumb"]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", BROWSER_UA), ("Accept", "*/*"), ("Accept-Language", "en-US,en;q=0.9")]
    for u in ("https://fc.yahoo.com", "https://finance.yahoo.com/"):
        try:
            op.open(u, timeout=10).read()
        except Exception:  # noqa: BLE001  (fc.yahoo.com répond 404 mais pose le cookie)
            pass
    crumb = None
    try:
        crumb = op.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).read().decode().strip() or None
    except Exception as e:  # noqa: BLE001
        log(f"  ! Yahoo crumb : {e}")
    _YH.update(op=op, crumb=crumb)
    return op, crumb


def yahoo(symbol: str, rng: str, interval: str) -> dict:
    if _YH.get("blocked", 0) >= 2:  # Yahoo bloque ce runner : inutile d'insister
        raise RuntimeError("Yahoo indisponible (limite de requêtes)")
    op, crumb = yahoo_opener()
    q = urllib.parse.quote(symbol)
    err = None
    for attempt in range(2):
        host = ("query1", "query2")[attempt % 2]
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{q}?range={rng}&interval={interval}&includePrePost=false"
        if crumb:
            url += "&crumb=" + urllib.parse.quote(crumb)
        try:
            data = json.loads(op.open(url, timeout=12).read())
            res = data["chart"]["result"][0]
            ts = res.get("timestamp") or []
            closes = res["indicators"]["quote"][0].get("close") or []
            pts = [(t, c) for t, c in zip(ts, closes) if c is not None]
            if pts:
                _YH["blocked"] = 0
                return {"meta": res.get("meta", {}), "points": pts}
            err = "réponse vide"
        except Exception as e:  # noqa: BLE001
            err = e
            if getattr(e, "code", None) == 404:
                break
            time.sleep(2)
    _YH["blocked"] = _YH.get("blocked", 0) + 1
    raise RuntimeError(f"Yahoo {symbol} {rng}: {err}")


def from_yahoo(a: dict) -> dict:
    d = yahoo(a["symbol"], "5y", "1d")
    meta = d["meta"]
    daily: dict[str, float] = {}
    for t, c in d["points"]:  # une valeur par jour (la dernière l'emporte)
        daily[day(t + (meta.get("gmtoffset") or 0))] = c
    out = {"currency": meta.get("currency") or "", "daily": sorted(daily.items()),
           "price": meta.get("regularMarketPrice"), "source": "Yahoo Finance"}
    try:
        out["intraday"] = yahoo(a["symbol"], "5d", "30m")["points"]
    except Exception as e:  # noqa: BLE001
        log(f"  ! {a['name']} intraday : {e}")
    return out


def from_cnbc(a: dict) -> dict:
    """Indices, devises, matières premières : graphes CNBC (sans clé), 5 ans + 5 jours en 30 min."""
    sym = a.get("cnbc")
    if not sym:
        raise RuntimeError("pas de symbole CNBC")
    q = urllib.parse.quote(sym, safe="")
    base = "https://ts-api.cnbc.com/harmony/app/bars"
    start = (NOW - timedelta(days=5 * 365 + 7)).strftime("%Y%m%d000000")
    end = (NOW + timedelta(days=1)).strftime("%Y%m%d000000")
    bars = fetch_json(f"{base}/{q}/1D/{start}/{end}/adjusted/EST5EDT.json", ua=BROWSER_UA, retries=2)["barData"]["priceBars"]
    daily = {}
    for b in bars:
        if b.get("close") not in (None, ""):
            t = b["tradeTime"]
            daily[f"{t[:4]}-{t[4:6]}-{t[6:8]}"] = float(b["close"])
    series = sorted(daily.items())
    if not series:
        raise RuntimeError("aucune donnée")
    if "=" in sym:
        cur = a["symbol"].upper()[3:6] if re.fullmatch(r"[A-Z]{6}=X", a["symbol"].upper()) else "USD"
    else:
        cur = "USD" if sym.startswith("@") else ""
    out = {"currency": cur, "daily": series, "price": series[-1][1], "source": "CNBC"}
    try:
        s7 = (NOW - timedelta(days=7)).strftime("%Y%m%d000000")
        ib = fetch_json(f"{base}/{q}/30M/{s7}/{end}/adjusted/EST5EDT.json", ua=BROWSER_UA, retries=1)["barData"]["priceBars"]
        intr = [(int(b["tradeTimeinMills"]) // 1000, float(b["close"])) for b in ib if b.get("close") not in (None, "")]
        cutoff = NOW.timestamp() - 5 * 86400
        out["intraday"] = [x for x in intr if x[0] >= cutoff] or intr[-240:]
        if out["intraday"]:
            out["price"] = out["intraday"][-1][1]
    except Exception as e:  # noqa: BLE001
        log(f"  ! {a['name']} intraday CNBC : {e}")
    return out


def from_binance(a: dict) -> dict:
    """Crypto : chandelles journalières (5 ans) et 30 min du miroir public de données Binance."""
    m = re.fullmatch(r"([A-Z0-9]+)-USD", a["symbol"].upper())
    if not m:
        raise RuntimeError("pas une crypto en USD")
    pair = m.group(1) + "USDT"
    base = "https://data-api.binance.vision/api/v3/klines"
    start = int((NOW - timedelta(days=5 * 365 + 5)).timestamp() * 1000)
    daily = []
    while True:
        rows = fetch_json(f"{base}?symbol={pair}&interval=1d&limit=1000&startTime={start}", retries=2)
        daily += [(day(r[0] / 1000), float(r[4])) for r in rows]
        if len(rows) < 1000:
            break
        start = rows[-1][0] + 1
    if not daily:
        raise RuntimeError("aucune donnée")
    out = {"currency": "USD", "daily": daily, "price": daily[-1][1], "source": "Binance"}
    try:
        rows = fetch_json(f"{base}?symbol={pair}&interval=30m&limit=240", retries=1)
        out["intraday"] = [(r[0] // 1000, float(r[4])) for r in rows]
        if out["intraday"]:
            out["price"] = out["intraday"][-1][1]
    except Exception as e:  # noqa: BLE001
        log(f"  ! {a['name']} intraday Binance : {e}")
    return out


def from_ecb(a: dict) -> dict:
    """Devises contre l'euro : taux de référence quotidiens de la BCE (sans intraday)."""
    m = re.fullmatch(r"EUR([A-Z]{3})=X", a["symbol"].upper())
    if not m:
        raise RuntimeError("pas une paire EUR/xxx")
    quote = m.group(1)
    start = (NOW - timedelta(days=5 * 365 + 5)).strftime("%Y-%m-%d")
    raw = fetch(f"https://data-api.ecb.europa.eu/service/data/EXR/D.{quote}.EUR.SP00.A?startPeriod={start}&format=csvdata", retries=2).decode()
    rows = list(csv.DictReader(raw.splitlines()))
    daily = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in rows if r.get("OBS_VALUE"))
    return {"currency": quote, "daily": daily, "price": daily[-1][1], "source": "BCE"}


def from_coingecko(a: dict) -> dict:
    if not a.get("gecko"):
        raise RuntimeError("pas d'identifiant CoinGecko")
    cur = "usd" if a["symbol"].upper().endswith("USD") else "eur"
    d = fetch_json(f"https://api.coingecko.com/api/v3/coins/{a['gecko']}/market_chart?vs_currency={cur}&days=365&interval=daily", retries=2)
    daily: dict[str, float] = {}
    for t, p in d["prices"]:
        daily[day(t / 1000)] = p
    series = sorted(daily.items())
    return {"currency": cur.upper(), "daily": series, "price": series[-1][1], "source": "CoinGecko (1 an)"}


def collect_asset(a: dict, prev: dict | None) -> dict:
    t0 = time.time()
    out = {**a}
    got, errors = None, []
    for provider in (from_cnbc, from_binance, from_coingecko, from_ecb, from_yahoo):
        try:
            got = provider(a)
            if got["daily"]:
                break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{provider.__name__[5:]}: {e}")
            got = None
    if not got:
        log(f"  ! {a['name']} ({a['symbol']}, {time.time() - t0:.0f} s) : {' | '.join(errors)}")
        if prev and prev.get("daily"):
            return {**prev, **a, "stale": True}
        return {**out, "daily": [], "intraday": [], "price": None, "perf": {}, "stale": True}

    series = got["daily"]
    price = got.get("price") or series[-1][1]
    today = NOW.strftime("%Y-%m-%d")
    intr = got.get("intraday") or []
    if series[-1][0] == today:
        series[-1] = (today, price)
    elif intr and day(intr[-1][0]) > series[-1][0]:  # séance du jour pas encore clôturée
        series.append((day(intr[-1][0]), price))
    ref = price
    out["daily"] = [[k, rnd(v, ref)] for k, v in series]
    out["intraday"] = [[int(t), rnd(c, ref)] for t, c in got.get("intraday", [])]
    out["currency"] = got.get("currency") or (prev or {}).get("currency") or ("EUR" if a["symbol"].endswith(".PA") else "")
    out["price"] = rnd(price, ref)
    out["source"] = got["source"]

    def back(days: int) -> float:
        lim = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
        prior = [v for k, v in series if k <= lim]
        return prior[-1] if prior else series[0][1]

    ytd_prior = [v for k, v in series if k <= f"{NOW.year - 1}-12-31"]
    prev_close = series[-2][1] if len(series) > 1 else None
    out["perf"] = {
        "1J": pct(price, prev_close), "1S": pct(price, back(7)), "1M": pct(price, back(30)),
        "YTD": pct(price, ytd_prior[-1]) if ytd_prior else None, "1A": pct(price, back(365)),
        "5A": pct(price, series[0][1]) if len(series) > 4 * 250 else None,
    }
    log(f"- {a['name']} [{got['source']}, {time.time() - t0:.0f} s] : {out['price']} {out['currency']} ({out['perf'].get('1J')} %)")
    return out


# --------------------------------------------------------------------------- crypto
def collect_crypto(prev: dict, stable_ids: set[str]) -> dict:
    out = dict(prev or {})
    try:
        url = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc"
               "&per_page=40&page=1&sparkline=true&price_change_percentage=1h,24h,7d,30d,1y")
        rows = fetch_json(url)
        top = []
        for c in rows:
            name = c.get("name") or ""
            if c.get("id") in stable_ids or re.search(r"\b(wrapped|staked|bridged|restaked|heloc|tokenized|treasury|liquid staking)\b", name, re.I) \
                    or (c.get("symbol") or "").lower() in NOT_COINS:
                continue
            spark = (c.get("sparkline_in_7d") or {}).get("price") or []
            step = max(1, len(spark) // 56)
            price = c.get("current_price")
            top.append({
                "id": c["id"], "symbol": (c.get("symbol") or "").upper(), "name": name, "image": c.get("image"),
                "rank": c.get("market_cap_rank"), "price": price, "mcap": c.get("market_cap"),
                "volume": c.get("total_volume"),
                "ch1h": c.get("price_change_percentage_1h_in_currency"),
                "ch24h": c.get("price_change_percentage_24h_in_currency"),
                "ch7d": c.get("price_change_percentage_7d_in_currency"),
                "ch30d": c.get("price_change_percentage_30d_in_currency"),
                "ch1y": c.get("price_change_percentage_1y_in_currency"),
                "ath": c.get("ath"), "ath_change": c.get("ath_change_percentage"),
                "spark": [rnd(v, price) for v in spark[::step]],
            })
            if len(top) >= TOP_CRYPTO:
                break
        out["top"] = top
        log(f"- CoinGecko : top {len(top)}")
    except Exception as e:  # noqa: BLE001
        log(f"  ! CoinGecko markets : {e}")
    time.sleep(3)
    try:
        g = fetch_json("https://api.coingecko.com/api/v3/global")["data"]
        out["global"] = {
            "mcap": g["total_market_cap"].get("usd"), "volume": g["total_volume"].get("usd"),
            "mcap_change_24h": g.get("market_cap_change_percentage_24h_usd"),
            "dominance": {k: round(v, 2) for k, v in sorted(g["market_cap_percentage"].items(), key=lambda x: -x[1])[:6]},
            "coins": g.get("active_cryptocurrencies"),
        }
    except Exception as e:  # noqa: BLE001
        log(f"  ! CoinGecko global : {e}")
    try:
        f = fetch_json("https://api.alternative.me/fng/?limit=730")["data"]
        out["fng"] = sorted([[day(int(x["timestamp"])), int(x["value"])] for x in f])
        out["fng_label"] = f[0].get("value_classification")
    except Exception as e:  # noqa: BLE001
        log(f"  ! Fear & Greed : {e}")
    try:
        tvl = fetch_json("https://api.llama.fi/v2/historicalChainTvl")
        out["tvl"] = [[day(int(x["date"])), round(x["tvl"])] for x in tvl if int(x["date"]) > NOW.timestamp() - 5 * 366 * 86400]
    except Exception as e:  # noqa: BLE001
        log(f"  ! TVL DeFi : {e}")
    return out


# --------------------------------------------------------------------------- stablecoins
def collect_stables(prev: dict, eurusd: dict[str, float], eurusd_now: float | None) -> dict:
    out = dict(prev or {})
    try:
        assets = fetch_json("https://stablecoins.llama.fi/stablecoins?includePrices=true")["peggedAssets"]
    except Exception as e:  # noqa: BLE001
        log(f"  ! DefiLlama stablecoins : {e}")
        return out

    def circ(obj, key):
        return (obj or {}).get(key) or 0

    rows = []
    for s in assets:
        peg = s.get("pegType")
        if peg not in ("peggedUSD", "peggedEUR") or (s.get("symbol") or "").upper() in NOT_STABLES:
            continue
        c = circ(s.get("circulating"), peg)
        price = s.get("price")
        target = 1.0 if peg == "peggedUSD" else eurusd_now
        mcap = c * (price or (target or 1)) if peg == "peggedEUR" else c * (price if price and 0.5 < price < 1.5 else 1)
        dev = (price / target - 1) if price and target else None
        rows.append({
            "id": s.get("id"), "gecko": s.get("gecko_id"), "name": s.get("name"), "symbol": s.get("symbol"),
            "peg": "EUR" if peg == "peggedEUR" else "USD", "mechanism": s.get("pegMechanism"),
            "price": price, "deviation": round(dev * 100, 3) if dev is not None else None,
            "mcap": round(mcap), "circ": round(c),
            "ch1d": pct(c, circ(s.get("circulatingPrevDay"), peg)),
            "ch7d": pct(c, circ(s.get("circulatingPrevWeek"), peg)),
            "ch30d": pct(c, circ(s.get("circulatingPrevMonth"), peg)),
            "chains": len(s.get("chains") or []),
        })
    rows.sort(key=lambda r: -r["mcap"])
    total = sum(r["mcap"] for r in rows)
    usd = [r for r in rows if r["peg"] == "USD"][:TOP_STABLES]
    eur = [r for r in rows if r["peg"] == "EUR"][:4]
    top = sorted(usd + eur, key=lambda r: -r["mcap"])
    for r in top:
        r["share"] = round(r["mcap"] / total * 100, 2) if total else None
        r["alert"] = r["deviation"] is not None and abs(r["deviation"]) >= DEPEG_ALERT * 100
    out["top"] = top
    out["total"] = total
    out["eur_total"] = sum(r["mcap"] for r in rows if r["peg"] == "EUR")
    out["count"] = len(rows)
    log(f"- Stablecoins : {len(rows)} suivis, capi {total / 1e9:.1f} Md$")

    try:
        chart = fetch_json("https://stablecoins.llama.fi/stablecoincharts/all")
        hist = []
        for x in chart:
            t = int(x["date"])
            if t < NOW.timestamp() - 5 * 366 * 86400:
                continue
            v = sum((x.get("totalCirculatingUSD") or {}).values())
            if v:
                hist.append([day(t), round(v)])
        out["history"] = hist
        if len(hist) > 31:
            out["total_ch7d"] = pct(hist[-1][1], hist[-8][1])
            out["total_ch30d"] = pct(hist[-1][1], hist[-31][1])
    except Exception as e:  # noqa: BLE001
        log(f"  ! DefiLlama historique : {e}")

    try:
        prices = fetch_json("https://stablecoins.llama.fi/stablecoinprices")
        cutoff = NOW.timestamp() - 366 * 86400
        wanted = {r["gecko"]: r for r in top if r.get("gecko")}
        peg_hist: dict[str, list] = {g: [] for g in wanted}
        for x in prices:
            t = int(x["date"])
            if t < cutoff:
                continue
            d = day(t)
            for g, r in wanted.items():
                p = (x.get("prices") or {}).get(g)
                if not p:
                    continue
                if r["peg"] == "EUR":
                    rate = eurusd.get(d)
                    if not rate:
                        continue
                    p = p / rate
                if 0.5 < p < 1.5:
                    peg_hist[g].append([d, round(p, 5)])
        out["peg_history"] = {g: v for g, v in peg_hist.items() if len(v) > 10}
    except Exception as e:  # noqa: BLE001
        log(f"  ! DefiLlama prix : {e}")
    return out


# --------------------------------------------------------------------------- actualités
def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " " + re.sub(r"[^a-z0-9.+]+", " ", s).strip() + " "


class NewsFilter:
    """Écarte les articles hors sujet ou commerciaux (pubs, promos, prédictions de cours…)."""

    def __init__(self, cfg: dict):
        self.title_re = re.compile("|".join(cfg.get("titres_exclus", [])) or r"(?!x)x", re.I)
        self.sources = [norm(x) for x in cfg.get("sources_exclues", [])]
        self.dropped = 0

    def ok(self, item: dict, terms: list[str]) -> bool:
        title = item["title"]
        if self.title_re.search(title):
            return self._drop()
        src = norm(item.get("source") or "") + norm(urllib.parse.urlparse(item["url"]).netloc)
        if any(b in src for b in self.sources):
            return self._drop()
        if terms and not any(t in norm(title) for t in terms):
            return self._drop()
        return True

    def _drop(self) -> bool:
        self.dropped += 1
        return False


def parse_feed(xml_bytes: bytes) -> list[dict]:
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter():
        if it.tag.split("}")[-1] != "item":
            continue
        def get(name, _it=it):
            for c in _it:
                if c.tag.split("}")[-1] == name:
                    return c
            return None
        title_el, link_el, date_el, src_el = get("title"), get("link"), get("pubDate"), get("source")
        title = clean_text(title_el.text if title_el is not None else "", 220)
        link = (link_el.text or "").strip() if link_el is not None else ""
        source = src_el.text.strip() if src_el is not None and src_el.text else None
        if title and source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")]
        if not title or not link:
            continue
        items.append({"title": title, "url": link, "source": source,
                      "date": iso(parse_date(date_el.text if date_el is not None else None))})
    return items


def google_news(query: str, lang: str, negative: str) -> list[dict]:
    params = {"fr": "hl=fr&gl=FR&ceid=FR:fr", "en": "hl=en-US&gl=US&ceid=US:en"}[lang]
    q = " ".join(x for x in (f"({query})", negative, f"when:{NEWS_WINDOW_DAYS}d") if x)
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&{params}"
    try:
        return [dict(i, lang=lang) for i in parse_feed(fetch(url))]
    except Exception as e:  # noqa: BLE001
        log(f"  ! news {lang} '{query[:40]}': {e}")
        return []


def collect_theme(theme: dict, flt: NewsFilter, fcfg: dict) -> dict:
    negative = " ".join(f"-{w}" for w in fcfg.get("mots_exclus_recherche", []))
    news = google_news(theme["query_fr"], "fr", negative) + google_news(theme["query_en"], "en", negative)
    # Termes devant figurer dans le titre, en mots entiers (« Fed » ≠ « fédération »).
    terms = [" " + norm(t).strip() + " " for t in theme.get("match", [theme["name"]])]
    extra = re.compile("|".join(theme["exclure"]), re.I) if theme.get("exclure") else None
    raw = len(news)
    news = [n for n in news if flt.ok(n, terms) and not (extra and extra.search(n["title"]))]
    cutoff = NOW - timedelta(days=NEWS_WINDOW_DAYS)
    seen_u, seen_t, kept = set(), set(), []
    for n in sorted(news, key=lambda x: x["date"] or "", reverse=True):
        d = parse_date(n["date"])
        if d and d < cutoff:
            continue
        kt = re.sub(r"\W+", "", n["title"].lower())[:70]
        if n["url"] in seen_u or kt in seen_t:
            continue
        seen_u.add(n["url"]); seen_t.add(kt)
        kept.append(n)
    # Mélange FR / EN : au moins un tiers d'articles en français si disponibles.
    fr = [n for n in kept if n["lang"] == "fr"][: MAX_NEWS // 2]
    rest = [n for n in kept if n not in fr][: MAX_NEWS - len(fr)]
    final = sorted(fr + rest, key=lambda x: x["date"] or "", reverse=True)
    log(f"- Actus « {theme['name']} » : {len(final)} retenues / {raw}")
    return {"name": theme["name"], "news": final, "news_count": len(kept)}


def mark_new(themes: list[dict], history: dict) -> dict:
    today = NOW.strftime("%Y-%m-%d")
    first_run = not history
    for t in themes:
        for item in t["news"]:
            first = history.setdefault(item["url"], today)
            item["new"] = first == today and not first_run
            if first_run:
                d = parse_date(item.get("date"))
                item["new"] = bool(d and NOW - d < timedelta(hours=24))
        t["new_count"] = sum(1 for i in t["news"] if i.get("new"))
    limit = (NOW - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    return {u: d for u, d in history.items() if d >= limit}


# --------------------------------------------------------------------------- pipeline
def render(payload: dict) -> str:
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return tpl.replace("/*__DATA__*/null", blob)


def movers(assets: list[dict], crypto: dict) -> dict:
    pool = [{"name": a["name"], "kind": a["category"], "ch": a["perf"].get("1J"), "symbol": a["symbol"]}
            for a in assets if a.get("perf", {}).get("1J") is not None]
    known = {a["gecko"] for a in assets if a.get("gecko")}
    pool += [{"name": c["name"], "kind": "Crypto", "ch": c["ch24h"], "gecko": c["id"]}
             for c in crypto.get("top", []) if c.get("ch24h") is not None and c["id"] not in known]
    pool.sort(key=lambda x: x["ch"])
    return {"down": [p for p in pool[:4] if p["ch"] < 0], "up": [p for p in pool[::-1][:4] if p["ch"] > 0]}


def main() -> int:
    previous = {}
    if DATA_PATH.exists():
        try:
            previous = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    cfg = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    fcfg = cfg.get("_filtres", {})
    flt = NewsFilter(fcfg)
    assets_cfg = read_assets()
    log(f"{len(assets_cfg)} actifs à suivre")

    prev_assets = {a["symbol"]: a for a in previous.get("assets", [])}
    with ThreadPoolExecutor(max_workers=2) as pool:
        themes_f = pool.submit(lambda: [collect_theme(t, flt, fcfg) for t in cfg.get("themes", [])])
        assets = []
        for a in assets_cfg:  # séquentiel : Yahoo limite fortement les requêtes parallèles
            assets.append(collect_asset(a, prev_assets.get(a["symbol"])))
            time.sleep(0.8)

        eur = next((a for a in assets if a["symbol"].upper() == "EURUSD=X" and a.get("daily")), None)
        eurusd = {d: v for d, v in eur["daily"]} if eur else {}
        stables = collect_stables(previous.get("stables", {}), eurusd, eur["price"] if eur else None)
        stable_ids = {s["gecko"] for s in stables.get("top", []) if s.get("gecko")}
        crypto = collect_crypto(previous.get("crypto", {}), stable_ids)
        themes = themes_f.result()

    if not any(t["news"] for t in themes) and previous.get("themes"):
        log("Aucune actualité collectée : on conserve celles du passage précédent.")
        themes = previous["themes"]
    history = mark_new(themes, previous.get("history", {}))
    log(f"Filtre actualités : {flt.dropped} articles écartés")

    if not any(a.get("daily") for a in assets) and not crypto.get("top") and previous:
        log("Aucune donnée de marché collectée (réseau ?) : on conserve la version précédente.")
        return 1

    alerts = [s for s in stables.get("top", []) if s.get("alert")]
    payload = {
        "generated_at": iso(NOW),
        "assets": assets,
        "crypto": crypto,
        "stables": stables,
        "themes": themes,
        "movers": movers(assets, crypto),
        "alerts": [{"name": s["name"], "symbol": s["symbol"], "deviation": s["deviation"], "peg": s["peg"]} for s in alerts],
        "stats": {
            "assets": len(assets),
            "news": sum(len(t["news"]) for t in themes),
            "new": sum(t["new_count"] for t in themes),
            "stale": [a["name"] for a in assets if a.get("stale")],
        },
    }
    DATA_PATH.parent.mkdir(exist_ok=True)
    DATA_PATH.write_text(json.dumps({**payload, "history": history}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    OUT_PATH.write_text(render(payload), encoding="utf-8")
    log(f"OK : {payload['stats']} · {len(OUT_PATH.read_bytes()) / 1024:.0f} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
