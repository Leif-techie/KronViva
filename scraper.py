"""
KronViva (Göteborg) – scraper för onsdagstävlingar på Svenska Bridgeförbundet.

Varje spelkväll har två resultatlistor: handicap (huvudsidan tvl/ID) och scratch (tvl/ID/scratch).
Båda sparas per par; frontenden växlar vilken procent som styr snitt och ranking.
"""

import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CLUB_PATH = "kronviva"
CLUB_HCP_PATH = f"{CLUB_PATH}/listor/handikapp"
BASE_URL = f"https://www.svenskbridge.se/{CLUB_PATH}/tvl/{{}}"
CLUB_HCP_URL = f"https://www.svenskbridge.se/{CLUB_HCP_PATH}"

# Tävlingar registrerade under annan klubb men som räknas som KronViva-onsdagar.
# Formatet är "klubb-slug" -> lista av tvl-ID:n.
ALT_CLUB_PATHS: dict[str, str] = {}  # tvl_id (str) -> club_slug

# Datumrad i HTML: "2026-04-22 … KronViva …" – klubbnamnet kan ligga inuti en anchor-tagg.
# Matchar båda varianterna: med och utan <a href="/kronviva/...">…</a>.
CLUB_COMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:\s|&nbsp;)+(?:<a\s[^>]*/kronviva[^>]*>)?KronViva",
    re.IGNORECASE,
)

# Matchar datum direkt efter pbn_header för valfri klubb (används för alt-klubb-ID:n).
DATE_HEADER_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\s|&nbsp;)")

TARGET_WEEKDAY = 2  # onsdag
WEEKDAYS_SV = (
    "Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"
)

# När gamla tvl-länkar försvinner från klubbsidan kan id listas här eller i data/extra_tvl_ids.txt
ADDITIONAL_TVL_IDS: list[int] = []


def discover_tvl_ids_from_club_index() -> set[int]:
    ids: set[int] = set()
    try:
        r = requests.get(f"https://www.svenskbridge.se/{CLUB_PATH}", timeout=25)
        r.raise_for_status()
        for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
            m = re.search(rf"/{re.escape(CLUB_PATH)}/tvl/(\d+)", a["href"])
            if m:
                ids.add(int(m.group(1)))
    except OSError as e:
        logger.warning("Kunde inte lista tävlingar från klubbsida: %s", e)
    return ids


def _load_ids_from_file(path: str) -> set[int]:
    p = Path(path)
    if not p.exists():
        return set()
    out: set[int] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            out.add(int(line))
    return out


def load_extra_tvl_ids() -> set[int]:
    return _load_ids_from_file("data/extra_tvl_ids.txt")


def load_alt_club_tvl_ids() -> dict[int, str]:
    """Laddar tävlingar registrerade under annan klubb (t.ex. BK Lejonet).
    Returnerar dict: tvl_id -> club_slug."""
    result: dict[int, str] = {}
    for slug in ["bk-lejonet"]:
        for tvl_id in _load_ids_from_file(f"data/{slug}_tvl_ids.txt"):
            result[tvl_id] = slug
    return result


def all_tvl_ids_to_fetch() -> list[tuple[int, str]]:
    """Returnerar lista av (tvl_id, club_slug) att hämta."""
    standard_ids = (
        discover_tvl_ids_from_club_index()
        | load_extra_tvl_ids()
        | set(ADDITIONAL_TVL_IDS)
    )
    alt_ids = load_alt_club_tvl_ids()
    # Alt-klubb-ID:n prioriteras med sin egna slug; övriga får CLUB_PATH.
    result: dict[int, str] = {tvl_id: CLUB_PATH for tvl_id in standard_ids}
    result.update(alt_ids)
    return sorted(result.items())


def _competition_h1(soup: BeautifulSoup) -> str:
    for h in soup.find_all("h1"):
        t = h.get_text(strip=True)
        if t and t != "Svenska Bridgeförbundet":
            return t
    return "Onsdagsbridge"


def _tvl_url(comp_id: int, club_slug: str = CLUB_PATH) -> str:
    return f"https://www.svenskbridge.se/{club_slug}/tvl/{comp_id}"


def fetch_scratch_pcts(comp_id: int, club_slug: str = CLUB_PATH) -> dict[str, float]:
    """Scratch-procent per parnamn (kolumn Namn med ' - ')."""
    url = _tvl_url(comp_id, club_slug) + "/scratch"
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")
        result: dict[str, float] = {}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 4:
                    try:
                        pair_name = cells[3].get_text(strip=True)
                        scratch_pct = float(
                            cells[2].get_text(strip=True).replace(",", ".")
                        )
                        if pair_name:
                            result[pair_name] = scratch_pct
                    except (ValueError, IndexError):
                        continue
        return result
    except OSError as e:
        logger.warning("Fel vid scratch för tävling %s: %s", comp_id, e)
        return {}


def fetch_competition(comp_id: int, club_slug: str = CLUB_PATH) -> dict | None:
    url = _tvl_url(comp_id, club_slug)
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return None

        raw = resp.text

        # För tävlingar under KronVivas eget konto krävs att KronViva syns i datumraden.
        # För tävlingar under annan klubb (t.ex. BK Lejonet) räcker det med datumet.
        if club_slug == CLUB_PATH:
            if "KronViva" not in raw:
                return None
            m = CLUB_COMP_RE.search(raw)
            if not m:
                return None
            actual_date = m.group(1)
        else:
            m = DATE_HEADER_RE.search(raw)
            if not m:
                return None
            actual_date = m.group(1)

        try:
            comp_date = date.fromisoformat(actual_date)
        except ValueError:
            return None

        if comp_date < date(2026, 1, 1):
            return None
        if comp_date > date.today():
            return None
        if comp_date.weekday() != TARGET_WEEKDAY:
            logger.info("    Hoppar (inte onsdag): %s", actual_date)
            return None

        day_type = WEEKDAYS_SV[comp_date.weekday()]
        soup = BeautifulSoup(raw, "html.parser")
        title = _competition_h1(soup)

        time.sleep(0.2)
        scratch_pcts = fetch_scratch_pcts(comp_id, club_slug)

        pairs: list[dict] = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                # Tabellen kan ha 7 eller 8 celler beroende på om handicapkolumnen finns.
                # 7 celler: [plac, poäng, hcp_pct, parnamn, parnamn_alt, klubb1, klubb2]
                # 8 celler: [plac, poäng, hcp_pct, parnamn, parnamn_alt, pair_hcp, klubb1, klubb2]
                if len(cells) < 7:
                    continue
                try:
                    placement = int(cells[0].get_text(strip=True))
                    hcp_pct = float(cells[2].get_text(strip=True).replace(",", "."))
                    pair_name = cells[3].get_text(strip=True)
                    if len(cells) >= 8:
                        hcp_text = cells[5].get_text(strip=True).replace(",", ".")
                        hcp = float(hcp_text) if hcp_text not in ("", "-", "—") else None
                    else:
                        hcp = None
                except (ValueError, IndexError):
                    continue

                if " - " in pair_name:
                    parts = pair_name.split(" - ", 1)
                    sp1, sp2 = parts[0].strip(), parts[1].strip()
                else:
                    sp1, sp2 = pair_name, ""

                scratch_pct = scratch_pcts.get(pair_name)

                pairs.append({
                    "placement": placement,
                    "hcp_pct": hcp_pct,
                    "scratch_pct": scratch_pct,
                    "spelare1": sp1,
                    "spelare2": sp2,
                    "pair_hcp": hcp,
                })

        if not pairs:
            return None

        return {
            "id": comp_id,
            "date": actual_date,
            "day_type": day_type,
            "title": title,
            "pairs": pairs,
        }

    except OSError as e:
        logger.warning("Fel vid tävling %s: %s", comp_id, e)
        return None


def fetch_all_results(
    birthdates: dict | None = None,
    handicaps: dict | None = None,
    fetch_hcp: bool = True,
) -> dict:
    if fetch_hcp and handicaps is None:
        handicaps = fetch_club_handicaps()

    today = date.today()
    all_competitions: list[dict] = []

    id_list = all_tvl_ids_to_fetch()
    logger.info("Hämtar %s tvl-IDs (klubbindex + ev. extra lista)…", len(id_list))

    for comp_id, club_slug in id_list:
        logger.info("  tvl %s [%s]…", comp_id, club_slug)
        result = fetch_competition(comp_id, club_slug)
        if result:
            all_competitions.append(result)
            n_scr = sum(1 for p in result["pairs"] if p.get("scratch_pct") is not None)
            logger.info(
                "    OK – %s par, varav %s med scratch-mappning",
                len(result["pairs"]),
                n_scr,
            )
        else:
            logger.info("    överhoppad / inga resultat")
        time.sleep(0.3)

    all_competitions.sort(key=lambda c: c["date"])

    player_data: dict[str, dict] = defaultdict(
        lambda: {"wednesday_comps": 0, "competitions": []}
    )

    for comp in all_competitions:
        for pair in comp["pairs"]:
            for spelare, partner in (
                (pair["spelare1"], pair["spelare2"]),
                (pair["spelare2"], pair["spelare1"]),
            ):
                if not spelare:
                    continue
                pd = player_data[spelare]
                pd["wednesday_comps"] += 1
                pd["competitions"].append({
                    "date": comp["date"],
                    "day_type": comp["day_type"],
                    "title": comp["title"],
                    "comp_id": comp["id"],
                    "placement": pair["placement"],
                    "hcp_pct": pair["hcp_pct"],
                    "scratch_pct": pair["scratch_pct"],
                    "pair_hcp": pair["pair_hcp"],
                    "partner": partner,
                })

    players: list[dict] = []
    for name, pd in player_data.items():
        comps = pd["competitions"]
        if not comps:
            continue

        avg_hcp_pct = sum(c["hcp_pct"] for c in comps) / len(comps)
        scratch_vals = [c["scratch_pct"] for c in comps if c.get("scratch_pct") is not None]
        avg_scratch_pct = (
            sum(scratch_vals) / len(scratch_vals) if scratch_vals else None
        )

        hcp_values = [c["pair_hcp"] for c in comps if c["pair_hcp"] is not None]
        avg_hcp = sum(hcp_values) / len(hcp_values) if hcp_values else None

        bd = birthdates.get(name) if birthdates else None
        age = None
        if bd:
            try:
                birth = date.fromisoformat(bd)
                td = date.today()
                age = td.year - birth.year - ((td.month, td.day) < (birth.month, birth.day))
            except ValueError:
                pass

        individual_hcp = handicaps.get(name) if handicaps else None

        players.append({
            "name": name,
            # Bakåtkompatibilitet: avg_pct = hcp-snitt (tidigare "pct")
            "avg_pct": round(avg_hcp_pct, 2),
            "avg_hcp_pct": round(avg_hcp_pct, 2),
            "avg_scratch_pct": round(avg_scratch_pct, 2)
            if avg_scratch_pct is not None
            else None,
            "total_comps": len(comps),
            "wednesday_comps": pd["wednesday_comps"],
            "hcp": individual_hcp,
            "avg_pair_hcp": round(avg_hcp, 1) if avg_hcp is not None else None,
            "birthdate": bd,
            "age": age,
            "competitions": sorted(comps, key=lambda c: c["date"]),
        })

    players.sort(key=lambda p: p["avg_pct"], reverse=True)
    for i, p in enumerate(players, 1):
        p["rank"] = i

    return {
        "players": players,
        "meta": {
            "last_updated": datetime.now().isoformat(),
            "total_competitions": len(all_competitions),
            "wednesday_competitions": len(all_competitions),
            "period_start": "2026-01-01",
            "period_end": today.isoformat(),
            "club": "KronViva",
        },
        "competitions": [
            {
                "id": c["id"],
                "date": c["date"],
                "day_type": c["day_type"],
                "title": c["title"],
                "pairs": len(c["pairs"]),
            }
            for c in all_competitions
        ],
    }


def _fetch_handicap_list(url: str, label: str) -> dict[str, float]:
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            logger.warning("Kunde inte hämta handicaplista %s (status %s)", label, resp.status_code)
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")
        handicaps: dict[str, float] = {}
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 4:
                try:
                    namn = cells[2].get_text(strip=True)
                    hcp = float(cells[3].get_text(strip=True).replace(",", "."))
                    if namn:
                        handicaps[namn] = hcp
                except (ValueError, IndexError):
                    continue
        logger.info("Hämtade handicap för %s spelare från %s", len(handicaps), label)
        return handicaps
    except OSError as e:
        logger.warning("Fel vid handicaplista %s: %s", label, e)
        return {}


def fetch_club_handicaps() -> dict[str, float]:
    """Hämtar handicap från GBF (bred täckning) kompletterat med KronVivas egen lista."""
    gbf_url = "https://www.svenskbridge.se/g%C3%B6teborgs-bf/listor/handikapp"
    gbf = _fetch_handicap_list(gbf_url, "GBF")
    kv = _fetch_handicap_list(CLUB_HCP_URL, "KronViva")
    # KronVivas lista prioriteras vid konflikt (mer specifik)
    return {**gbf, **kv}


def load_birthdates(path: str = "birthdates.json") -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_results(results: dict, path: str = "data/results.json") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info("Resultat sparade i %s", path)


def load_results(path: str = "data/results.json") -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    birthdates = load_birthdates()
    results = fetch_all_results(birthdates)
    save_results(results)
    print(
        f"\nKlart! {results['meta']['total_competitions']} onsdagstävlingar, "
        f"{len(results['players'])} spelare."
    )
