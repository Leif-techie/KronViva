"""
Snabb parallell sokning efter KronViva onsdagstavlingar 2026.
Kor: python find_ids_parallel.py
Skriver funna ID:n till data/extra_tvl_ids.txt.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.svenskbridge.se/kronviva/tvl/{}"
REAL_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:\s|&nbsp;)+(?:<a\s[^>]*/kronviva[^>]*>)?KronViva",
    re.I,
)

# Sok i omradet dar Jan-Apr 2026 borde finnas
ID_START = 488127
ID_END   = 484000
WORKERS  = 8   # parallella trador (haller sig under SVB-begransning)


def check_id(cid: int):
    try:
        r = requests.get(BASE.format(cid), timeout=8)
        m = REAL_RE.search(r.text)
        if not m:
            return None
        d_str = m.group(1)
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            return None
        if d.year != 2026 or d.weekday() != 2:
            return None
        if "Inga resultat" in r.text:
            return (d_str, cid, 0)
        soup = BeautifulSoup(r.text, "html.parser")
        n = sum(
            1 for t in soup.find_all("table")
            for row in t.find_all("tr")
            if len(row.find_all("td")) >= 6
        )
        return (d_str, cid, n)
    except Exception:
        return None


def main():
    ids = list(range(ID_START, ID_END - 1, -1))
    print(f"Soker {len(ids)} ID:n med {WORKERS} parallella trador...\n")

    found_with_results = []
    found_no_results = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(check_id, cid): cid for cid in ids}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(ids)} klara, {len(found_with_results)} KronViva-onsdagar hittade")
            res = fut.result()
            if res:
                d_str, cid, n = res
                if n > 0:
                    print(f"  {cid}  {d_str}  {n} par  OK")
                    found_with_results.append((d_str, cid))
                else:
                    found_no_results.append((d_str, cid))

    found_with_results.sort()
    print(f"\n=== {len(found_with_results)} KronViva-onsdagar med resultat ===")
    for d, i in found_with_results:
        print(f'  ("{d}", {i}),')
    if found_no_results:
        found_no_results.sort()
        print(f"\n--- {len(found_no_results)} KronViva-onsdagar utan resultat (ej spelat?) ---")
        for d, i in found_no_results:
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
    new_ids = {i for _, i in found_with_results}
    all_ids = sorted(existing | new_ids)
    ids_path.write_text("\n".join(str(i) for i in all_ids) + "\n", encoding="utf-8")
    print(f"\nextra_tvl_ids.txt uppdaterad: {len(all_ids)} ID:n totalt.")


if __name__ == "__main__":
    main()
