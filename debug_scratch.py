import json

d = json.load(open("data/results.json", encoding="utf-8"))
for p in d["players"]:
    if "gblom" in p["name"].lower():
        print(f"Spelare: {p['name']}")
        for c in p["competitions"]:
            if "02-18" in c["date"]:
                print(f"  {c['date']}  hcp_plac={c.get('placement')}  "
                      f"scratch_plac={c.get('scratch_placement')}  "
                      f"scratch_pct={c.get('scratch_pct')}  hcp_pct={c.get('hcp_pct')}")
