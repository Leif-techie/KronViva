import json

d = json.load(open("data/results.json", encoding="utf-8"))

# Kompetitioner per datum
print("=== Alla 21 tävlingar ===")
for comp in sorted(d["competitions"], key=lambda c: c["date"]):
    print(f"  {comp['date']}  id:{comp['id']}  par:{comp['pairs']}  {comp['title']}")

# Hitta alla spelare som var med på jan-tävlingar (BK Lejonet)
jan_comp_ids = {c["id"] for c in d["competitions"] if c["date"].startswith("2026-01")}
feb_comp_ids = {c["id"] for c in d["competitions"] if c["date"].startswith("2026-02")}

print("\n=== Spelare aktiva i jan (BK Lejonet) ===")
jan_players = []
for p in d["players"]:
    comps_in_jan = [c for c in p["competitions"] if c["comp_id"] in jan_comp_ids]
    if comps_in_jan:
        jan_players.append((p["name"], len(comps_in_jan), p["total_comps"]))

for name, jan_count, total in sorted(jan_players, key=lambda x: -x[1])[:20]:
    print(f"  {name!r:40s}  jan:{jan_count}  totalt:{total}")

print("\n=== Enordsnamn i datan ===")
singles = [(p["name"], p["total_comps"]) for p in d["players"] if len(p["name"].split()) == 1]
for name, total in sorted(singles, key=lambda x: -x[1]):
    print(f"  {name!r}  totalt:{total}")
