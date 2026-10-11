"""Download Binance USD-M monthly funding rates and 1h perpetual candles
(2020-10 .. 2026-09) from data.binance.vision and write data/{btc,eth,sol}_funding.csv
and data/{btc,eth,sol}_perp_1h.csv. Needed by scripts/research_short_term.py.

    python scripts/fetch_binance_archive.py /tmp/binance_archive data
"""
import csv, io, os, sys, time, urllib.request, zipfile
arch, outdir = sys.argv[1], sys.argv[2]
BASE = "https://data.binance.vision/data/futures/um/monthly"
months = [(y, m) for y in range(2020, 2027) for m in range(1, 13) if (y, m) >= (2020, 10) and (y, m) <= (2026, 9)]
def get(url, path):
    if os.path.exists(path):
        return True
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            open(path, "wb").write(data)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    return False
for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
    fund, kl = {}, {}
    for y, m in months:
        tag = f"{y}-{m:02d}"
        fz = os.path.join(arch, f"{sym}-funding-{tag}.zip")
        if get(f"{BASE}/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip", fz):
            z = zipfile.ZipFile(fz)
            for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                if row and row[0].isdigit():
                    fund[int(row[0])] = float(row[2])
        kz = os.path.join(arch, f"{sym}-1h-{tag}.zip")
        if get(f"{BASE}/klines/{sym}/1h/{sym}-1h-{tag}.zip", kz):
            z = zipfile.ZipFile(kz)
            for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                if row and row[0].isdigit():
                    kl[int(row[0])] = row[1:6]
    with open(os.path.join(outdir, f"{sym[:-4].lower()}_funding.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts", "rate"])
        for ts in sorted(fund): w.writerow([ts, fund[ts]])
    with open(os.path.join(outdir, f"{sym[:-4].lower()}_perp_1h.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for ts in sorted(kl): w.writerow([ts] + kl[ts])
    print(sym, "funding rows", len(fund), "perp 1h rows", len(kl), flush=True)
