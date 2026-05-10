"""
Skannar SVB:s ID-rymd for att hitta alla KronViva-onsdagstavlingar 2026.
Kor:  python rebuild_ids.py
Skriver funna ID:n till data/extra_tvl_ids.txt (lases automatiskt av scraper.py).

SVB-URL:en /kronviva/tvl/{id} visar ALLTID KronViva i navigeringen oavsett
vilken klubb som ager tavlingen. Det riktiga testet ar att datumhuvudet
direkt foljs av "KronViva" utan en anchor-lank till annan klubb.
Monster: "2026-xx-xx &nbsp; ... &nbsp; KronViva &nbsp; ... Tavlings-ID"

Parametrar (andra nedan):
  ID_START  - borja soka har (inkl.)
  ID_END    - sluta soka har (inkl.)
  STEP      - riktning (-1 = bakat, +1 = framat)
"""

import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Parametrar
ID_START = 493000
ID_END   = 488129
STEP     = -1
PAUSE    = 0.20

BASE = "https://www.svenskbridge.se/kronviva/tvl/{}"

# Korrekt filter: KronViva direkt efter datumet (med eller utan anchor-tagg till /kronviva/).
# Matchar bade "2026-xx-xx ... KronViva" och "2026-xx-xx ... <a href=/kronviva/...>KronViva</a>".
REAL_KRONVIVA_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:\s|&nbsp;)+(?:<a\s[^>]*/kronviva[^>]*>)?KronViva",
    re.IGNORECASE,
)


def check(cid: int) -> tuple[str, int] | None:
    try:
        r = requests.get(BASE.format(cid), timeout=8)
    except OSError:
        return None
    txt = r.text

    m = REAL_KRONVIVA_RE.search(txt)
    if not m:
        return None
    d_str = m.group(1)
    try:
        d = date.fromisoformat(d_str)
    except ValueError:
        return None
    if d.year != 2026:
        return None
    if d.weekday() != 2:
        return None
    if "Inga resultat" in txt:
        print(f"  {cid}  {d_str}  (inga resultat)")
        return None
    soup = BeautifulSoup(txt, "html.parser")
    n_rows = sum(
        1 for t in soup.find_all("table")
        for row in t.find_all("tr")
        if len(row.find_all("td")) >= 6
    )
    if n_rows == 0:
        return None
    print(f"  {cid}  {d_str}  {n_rows} par")
    return (d_str, cid)


def main():
    found: list[tuple[str, int]] = []
    ids = range(ID_START, ID_END + STEP, STEP)
    print(f"Soker {len(ids)} ID:n ({ID_START} ner till {ID_END})...\n")
    for cid in ids:
        result = check(cid)
        if result:
            found.append(result)
        time.sleep(PAUSE)

    found.sort()
    print(f"\n=== Hittade {len(found)} KronViva-onsdagar ===")
    for d, i in found:
        print(f'  ("{d}", {i}),')

    ids_path = Path("data/extra_tvl_ids.txt")
    ids_path.parent.mkdir(exist_ok=True)
    existing: set[int] = set()
    if ids_path.exists():
        existing = {
            int(x)
            for x in ids_path.read_text(encoding="utf-8").splitlines()
            if x.strip().isdigit()
        }
    new_ids = {i for _, i in found}
    all_ids = sorted(existing | new_ids)
    ids_path.write_text("\n".join(str(i) for i in all_ids) + "\n", encoding="utf-8")
    print(f"\nextra_tvl_ids.txt uppdaterad: {len(all_ids)} ID:n totalt.")


if __name__ == "__main__":
    main()
