import json
d = json.load(open("data/results.json", encoding="utf-8"))
print(f"Tävlingar: {d['meta']['total_competitions']}  Spelare: {len(d['players'])}\n")
print("Topp 15 spelare:")
for p in d["players"][:15]:
    print(f"  {p['name']:35s} {p['total_comps']:2d} tävlingar  snitt {p['avg_hcp_pct']:.1f}%")

print("\nRobert Bäck och Lars Högblom:")
for p in d["players"]:
    if "ck" in p["name"] and "ober" in p["name"]:
        print(f"  {p['name']}: {p['total_comps']} tävlingar, datum: {[c['date'] for c in p['competitions']]}")
    if "gblom" in p["name"].lower():
        print(f"  {p['name']}: {p['total_comps']} tävlingar, datum: {[c['date'] for c in p['competitions']]}")
