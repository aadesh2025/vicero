"""Download the free DB-IP "IP to Country Lite" database for the pricing page's display currency.

    python scripts/fetch_geoip_db.py [output.mmdb]        # default: var/geoip/country.mmdb

No account or key needed. The file is MaxMind-format and updated monthly - re-run monthly (cron).
Licence: CC BY 4.0 - the pricing page shows the required "IP Geolocation by DB-IP" credit.
Set GEOIP_DB_PATH to the output path to turn the lookup on (ADR-112).
"""

from __future__ import annotations

import datetime as dt
import gzip
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

URL = "https://download.db-ip.com/free/dbip-country-lite-{ym}.mmdb.gz"


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "var/geoip/country.mmdb")
    out.parent.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    prev = (today.replace(day=1) - dt.timedelta(days=1))
    for month in (today, prev):  # the new month's file can lag by a day or two
        url = URL.format(ym=month.strftime("%Y-%m"))
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as tmp:
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
                    if r.status_code != 200:
                        print(f"{url}: HTTP {r.status_code}", file=sys.stderr)
                        continue
                    for chunk in r.iter_bytes():
                        tmp.write(chunk)
            except httpx.HTTPError as exc:
                print(f"{url}: {exc}", file=sys.stderr)
                continue
        with gzip.open(tmp.name, "rb") as src, tempfile.NamedTemporaryFile(delete=False, dir=out.parent) as dst:
            shutil.copyfileobj(src, dst)
        Path(dst.name).replace(out)  # atomic: a running API never sees a half-written file
        Path(tmp.name).unlink(missing_ok=True)
        print(f"wrote {out} ({out.stat().st_size // 1024} KB) from {url}")
        return 0
    print("could not download the database", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
