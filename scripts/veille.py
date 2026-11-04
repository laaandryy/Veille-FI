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
DEPEG_ALERT = 0.005         # écart à l'ancrage signalé au-delà de 0,5 %
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA = "veille-fi-bot/1.0 (+https://github.com/laaandryy/Veille-FI)"
TIMEOUT = 25


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
    raw = CSV_PATH.read_bytes().decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(raw.splitlines(), delimiter=";"))
    out = []
    for row in rows[1:]:
        row = [c.strip() for c in row] + ["", "", "", ""]
        if not row[0] or not row[1]:
            continue
        out.append({"name": row[0], "symbol": row[1], "category": row[2] or "Autres", "gecko": row[3] or None})
    return out


# --------------------------------------------------------------------------- marchés (Yahoo)
def yahoo(symbol: str, rng: str, interval: str) -> dict:
    q = urllib.parse.quote(symbol)
    err = None
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{q}?range={rng}&interval={interval}&includePrePost=false"
        try:
            data = fetch_json(url, ua=BROWSER_UA, retries=2)
            res = data["chart"]["result"][0]
            ts = res.get("timestamp") or []
            closes = res["indicators"]["quote"][0].get("close") or []
            pts = [(t, c) for t, c in zip(ts, closes) if c is not None]
            if pts:
                return {"meta": res.get("meta", {}), "points": pts}
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(f"Yahoo {symbol} {rng}: {err}")


def collect_asset(a: dict, prev: dict | None) -> dict:
    out = {**a}
    try:
        d = yahoo(a["symbol"], "5y", "1d")
        meta = d["meta"]
        daily: dict[str, float] = {}
        for t, c in d["points"]:          # une valeur par jour (la dernière l'emporte)
            daily[day(t + (meta.get("gmtoffset") or 0))] = c
        series = sorted(daily.items())
        price = meta.get("regularMarketPrice") or series[-1][1]
        if series and series[-1][1] != price and series[-1][0] == NOW.strftime("%Y-%m-%d"):
            series[-1] = (series[-1][0], price)
        ref = price
        out["daily"] = [[k, rnd(v, ref)] for k, v in series]
        out["currency"] = meta.get("currency") or ""
        out["price"] = rnd(price, ref)
        out["market_time"] = iso(datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc)) if meta.get("regularMarketTime") else None

        def back(days: int | None = None, ytd: bool = False) -> float | None:
            if ytd:
                lim = f"{NOW.year - 1}-12-31"
                prior = [v for k, v in series if k <= lim]
                return prior[-1] if prior else None
            lim = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")  # type: ignore[arg-type]
            prior = [v for k, v in series if k <= lim]
            return prior[-1] if prior else series[0][1]

        prev_close = series[-2][1] if len(series) > 1 else None
        out["perf"] = {
            "1J": pct(price, prev_close), "1S": pct(price, back(7)), "1M": pct(price, back(30)),
            "YTD": pct(price, back(ytd=True)), "1A": pct(price, back(365)), "5A": pct(price, series[0][1]),
        }
    except Exception as e:  # noqa: BLE001
        log(f"  ! {a['name']} ({a['symbol']}) : {e}")
        if prev:
            return {**prev, **a, "stale": True}
        return {**out, "daily": [], "intraday": [], "price": None, "perf": {}, "stale": True}
    try:
        d = yahoo(a["symbol"], "5d", "30m")
        out["intraday"] = [[int(t), rnd(c, ref)] for t, c in d["points"]]
    except Exception as e:  # noqa: BLE001
        log(f"  ! {a['name']} intraday : {e}")
        out["intraday"] = (prev or {}).get("intraday", [])
    log(f"- {a['name']} : {out['price']} {out['currency']} ({out['perf'].get('1J')} %)")
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
            if c.get("id") in stable_ids or re.search(r"\b(wrapped|staked|bridged|restaked)\b", name, re.I) \
                    or (c.get("symbol") or "").lower() in {"usdt", "usdc", "usde", "dai", "usds", "fdusd", "pyusd", "usd1", "steth", "wbtc", "weth", "wsteth", "weeth", "cbbtc", "bsc-usd"}:
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
        if peg not in ("peggedUSD", "peggedEUR"):
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
    terms = [" " + norm(t).strip() for t in theme.get("match", [theme["name"]])]
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
    with ThreadPoolExecutor(max_workers=4) as pool:
        assets = list(pool.map(lambda a: collect_asset(a, prev_assets.get(a["symbol"])), assets_cfg))
        themes_f = pool.submit(lambda: [collect_theme(t, flt, fcfg) for t in cfg.get("themes", [])])

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
