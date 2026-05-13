import requests, re
from bs4 import BeautifulSoup

# GBF kalender-noder för jan-feb KronViva-onsdagar
nodes = {
    "2026-01-14": 254713,
    "2026-01-21": 254714,
    "2026-01-28": 254715,
    "2026-02-04": 254784,
    "2026-02-11": 254788,
    "2026-02-18": 254792,
    "2026-02-25": 254794,
}

for datum, node in nodes.items():
    r = requests.get(f"https://www.svenskbridge.se/kalender/{node}", timeout=15)
    # Hitta ALLA tvl-länkar oavsett klubb
    tvl_ids = sorted(set(int(x) for x in re.findall(r"/(?:[\w-]+/)?tvl/(\d+)", r.text)))
    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else "?"
    print(f"\n{datum} /kalender/{node}")
    print(f"  Titel: {title}")
    print(f"  Alla tvl-IDs på sidan: {tvl_ids}")
    # Visa all text som innehåller "tvl"
    for a in soup.find_all("a", href=True):
        if "tvl" in a["href"]:
            print(f"  Länk: {a['href']}  text={a.get_text(strip=True)}")
