"""
Hämtar KronViva-tävlingsIDs från GBF-kalendern (jan-feb 2026)
och kontrollerar om de innehåller resultat på SVB.
"""
import re
import time
import requests
from bs4 import BeautifulSoup

MONTHS = ["2026-01", "2026-02", "2026-03"]
CLUB_COMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:\s|&nbsp;)+(?:<a\s[^>]*/kronviva[^>]*>)?KronViva",
    re.IGNORECASE,
)


def get_kronviva_calendar_nodes(month: str) -> list[tuple[str, str]]:
    """Returnerar lista av (datum, kalender-URL) för KronViva-tävlingar."""
    r = requests.get(
        f"https://www.svenskbridge.se/g%C3%B6teborgs-bf/kalender/{month}",
        timeout=15,
    )
    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    # Kalendern är en tabell med td[data-date] per dag
    for td in soup.find_all("td", attrs={"data-date": True}):
        date_str = td["data-date"]
        if "KronViva" not in td.get_text():
            continue
        for a in td.find_all("a", href=True):
            if "/kalender/" in a["href"]:
                results.append((date_str, a["href"]))
                break
    return results


def get_tvl_id_from_calendar_node(node_url: str) -> int | None:
    """Hämtar /kalender/NNNNN-sidan och letar efter tvl-ID."""
    url = f"https://www.svenskbridge.se{node_url}"
    r = requests.get(url, timeout=10)
    # Sök efter tvl-länk
    m = re.search(r"/(?:kronviva/)?tvl/(\d+)", r.text)
    return int(m.group(1)) if m else None


def main():
    print("Hämtar KronViva-tävlingar från GBF-kalendern...\n")
    all_nodes: list[tuple[str, str]] = []
    for month in MONTHS:
        nodes = get_kronviva_calendar_nodes(month)
        print(f"{month}: {len(nodes)} KronViva-tävlingar")
        for date_str, url in nodes:
            print(f"  {date_str}  {url}")
        all_nodes.extend(nodes)
        time.sleep(0.3)

    print("\nLetar upp tvl-IDs...\n")
    found = []
    for date_str, node_url in all_nodes:
        tvl_id = get_tvl_id_from_calendar_node(node_url)
        print(f"  {date_str}  {node_url}  ->  tvl-ID: {tvl_id}")
        if tvl_id:
            found.append(tvl_id)
        time.sleep(0.2)

    print(f"\nHittade {len(found)} tvl-IDs: {found}")


if __name__ == "__main__":
    main()
