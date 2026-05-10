import sys
sys.path.insert(0, ".")
from scraper import fetch_competition

test_cases = [
    (482299, "bk-lejonet"),
    (482300, "bk-lejonet"),
    (482377, "bk-lejonet"),
    (486369, "kronviva"),
]
for cid, slug in test_cases:
    r = fetch_competition(cid, slug)
    if r:
        print(f"OK: {r['date']}  {len(r['pairs'])} par  (ID {cid}, slug={slug})")
    else:
        print(f"MISSLYCKADES: {cid} [{slug}]")
