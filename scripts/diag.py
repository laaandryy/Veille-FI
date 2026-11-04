"""Diagnostic temporaire : quelles sources de cours répondent depuis GitHub Actions ?"""
import time, urllib.request
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
URLS = [
 "https://ts-api.cnbc.com/harmony/app/bars/.FCHI/1D/20211101000000/20261104000000/adjusted/EST5EDT.json",
 "https://ts-api.cnbc.com/harmony/app/bars/.SPX/30M/20261029000000/20261104230000/adjusted/EST5EDT.json",
 "https://ts-api.cnbc.com/harmony/app/bars/@GC.1/1D/20261001000000/20261104000000/adjusted/EST5EDT.json",
 "https://ts-api.cnbc.com/harmony/app/bars/EUR=/1D/20261001000000/20261104000000/adjusted/EST5EDT.json",
 "https://ts-api.cnbc.com/harmony/app/charts/5Y.json?symbol=.STOXX50E",
 "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=.FCHI|.SPX|@LCO.1|EUR=&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json",
 "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500",
 "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU",
 "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?startPeriod=2026-10-01&format=csvdata",
 "https://api.frankfurter.app/2026-10-01..?from=EUR&to=USD",
 "https://api.frankfurter.dev/v1/latest?base=EUR",
 "https://api.nasdaq.com/api/quote/SPX/historical?assetclass=index&fromdate=2026-10-01&limit=50",
 "https://live.euronext.com/en/ajax/AwlHistoricalPrice/getFullDownloadAjax/FR0003500008-XPAR",
 "https://min-api.cryptocompare.com/data/v2/histoday?fsym=BTC&tsym=USD&limit=5",
 "https://query1.finance.yahoo.com/v7/finance/spark?symbols=%5EFCHI&range=5d&interval=1d",
 "https://www.google.com/finance/quote/PX1:INDEXEURO",
]
for u in URLS:
    t = time.time()
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/json, text/csv, */*", "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=20) as r:
            b = r.read()
            print(f"OK {r.status} {len(b)}o {time.time()-t:.1f}s {u}\n   {b[:220]!r}", flush=True)
    except Exception as e:
        print(f"KO {time.time()-t:.1f}s {u}\n   {e}", flush=True)
