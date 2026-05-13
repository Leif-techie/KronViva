"""
KronViva (Göteborg) – scraper för onsdagstävlingar på Svenska Bridgeförbundet.

Varje spelkväll har två resultatlistor: handicap (huvudsidan tvl/ID) och scratch (tvl/ID/scratch).
Båda sparas per par; frontenden växlar vilken procent som styr snitt och ranking.
"""

import json
import logging
import re
import time
import unicodedata
from collections import Counter, defaultdict
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

# Parnamn "A - B" kan använda vanligt bindestreck, en-dash eller minus-tecken.
# Viktigt: bindestreck UTAN mellanslag ska INTE matchas (t.ex. "Sven-Olof" i ett namn).
PAIR_SPLIT_RE = re.compile(r"\s*[\u2013\u2212]\s*|\s+-\s+")


def normalize_player_key(name: str) -> str:
    """Stabil nyckel så t.ex. LARS HÖGBLOM och Lars Högblom slås ihop till en spelare."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = re.sub(r"\s+", " ", s.strip())
    return s.casefold()


def choose_display_name(votes: Counter) -> str:
    """Vanligast förekommande råsträng från SVB; vid lika längst sträng (oftast mest komplett)."""
    if not votes:
        return ""
    return sorted(votes.items(), key=lambda x: (-x[1], -len(x[0]), x[0]))[0][0]


def split_pair_name(pair_name: str) -> tuple[str, str]:
    parts = PAIR_SPLIT_RE.split(pair_name.strip(), maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return pair_name.strip(), ""


def discover_tvl_links_on_club_page(club_slug: str) -> set[int]:
    """Alla tvl-ID som länkas från klubbens startsida (roterande fönster – gamla id kan saknas)."""
    ids: set[int] = set()
    try:
        r = requests.get(f"https://www.svenskbridge.se/{club_slug}", timeout=25)
        r.raise_for_status()
        for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
            m = re.search(rf"/{re.escape(club_slug)}/tvl/(\d+)", a["href"])
            if m:
                ids.add(int(m.group(1)))
    except OSError as e:
        logger.warning("Kunde inte lista tävlingar från /%s/: %s", club_slug, e)
    return ids


def discover_tvl_ids_from_club_calendar() -> set[int]:
    """Skannar KronVivas egna kalender månad för månad och hämtar alla tvl-ID:n.
    Mer tillförlitlig än startsidan som bara visar ett roterande urval.
    """
    ids: set[int] = set()
    today = date.today()
    for year in [today.year]:
        for month in range(1, today.month + 1):
            month_str = f"{year}-{month:02d}"
            try:
                r = requests.get(
                    f"https://www.svenskbridge.se/{CLUB_PATH}/kalender/{month_str}",
                    timeout=15,
                )
                r.raise_for_status()
                # Hitta alla kalender-noder på sidan
                calendar_nodes = re.findall(r"/kalender/(\d{6,})", r.text)
                for node in set(calendar_nodes):
                    try:
                        r2 = requests.get(
                            f"https://www.svenskbridge.se/kalender/{node}",
                            timeout=10,
                        )
                        for m in re.finditer(
                            rf"/{re.escape(CLUB_PATH)}/tvl/(\d+)", r2.text
                        ):
                            ids.add(int(m.group(1)))
                        time.sleep(0.15)
                    except OSError:
                        pass
            except OSError as e:
                logger.warning("Kunde inte hämta kalender %s: %s", month_str, e)
    return ids


def discover_tvl_ids_from_club_index() -> set[int]:
    """Kombinerar kalender-skanning (fullständig) med startsidan (snabb, senaste)."""
    return discover_tvl_ids_from_club_calendar() | discover_tvl_links_on_club_page(CLUB_PATH)


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
    """Laddar tävlingar registrerade under annan klubb än KronViva.
    BK Lejonet är en separat klubb och ingår inte i KronVivas onsdagsbridge.
    """
    return {}


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


def fetch_scratch_data(comp_id: int, club_slug: str = CLUB_PATH) -> dict[str, dict]:
    """Scratch-data per parnamn: procent och placering från scratch-resultatlistan."""
    url = _tvl_url(comp_id, club_slug) + "/scratch"
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")
        result: dict[str, dict] = {}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 4:
                    try:
                        placement = int(cells[0].get_text(strip=True))
                        pair_name = cells[3].get_text(strip=True)
                        scratch_pct = float(
                            cells[2].get_text(strip=True).replace(",", ".")
                        )
                        if pair_name:
                            result[pair_name] = {"pct": scratch_pct, "placement": placement}
                    except (ValueError, IndexError):
                        continue
        return result
    except OSError as e:
        logger.warning("Fel vid scratch för tävling %s: %s", comp_id, e)
        return {}


def fetch_scratch_pcts(comp_id: int, club_slug: str = CLUB_PATH) -> dict[str, float]:
    """Scratch-procent per parnamn (bakåtkompatibelt)."""
    return {k: v["pct"] for k, v in fetch_scratch_data(comp_id, club_slug).items()}


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
        scratch_data = fetch_scratch_data(comp_id, club_slug)

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
                    hcp_placement = int(cells[0].get_text(strip=True))
                    hcp_pct = float(cells[2].get_text(strip=True).replace(",", "."))
                    pair_name = cells[3].get_text(strip=True)
                    if len(cells) >= 8:
                        hcp_text = cells[5].get_text(strip=True).replace(",", ".")
                        hcp = float(hcp_text) if hcp_text not in ("", "-", "—") else None
                    else:
                        hcp = None
                except (ValueError, IndexError):
                    continue

                sp1, sp2 = split_pair_name(pair_name)

                sd = scratch_data.get(pair_name, {})
                scratch_pct = sd.get("pct")
                scratch_placement = sd.get("placement")

                pairs.append({
                    "placement": hcp_placement,
                    "scratch_placement": scratch_placement,
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
        logger.info("Hämtar handicaplistor från SVB (GBF + KronViva)…")
        handicaps = fetch_club_handicaps()

    today = date.today()
    all_competitions: list[dict] = []

    logger.info("Söker tävlings-ID:n från KronViva- och BK Lejonet-sidan…")
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

    birthdates = birthdates or {}
    handicap_by_key: dict[str, float] = {}
    if handicaps:
        for hk, hv in handicaps.items():
            handicap_by_key[normalize_player_key(hk)] = hv
    birth_by_key: dict[str, str] = {}
    for bk, bv in birthdates.items():
        nk = normalize_player_key(bk)
        birth_by_key.setdefault(nk, bv)

    player_data: dict[str, dict] = defaultdict(
        lambda: {
            "wednesday_comps": 0,
            "competitions": [],
            "_name_votes": Counter(),
        }
    )

    for comp in all_competitions:
        for pair in comp["pairs"]:
            for spelare, partner in (
                (pair["spelare1"], pair["spelare2"]),
                (pair["spelare2"], pair["spelare1"]),
            ):
                if not spelare:
                    continue
                pname = normalize_player_key(spelare)
                if not pname:
                    continue
                pd = player_data[pname]
                pd["_name_votes"][spelare] += 1
                pd["wednesday_comps"] += 1
                pd["competitions"].append({
                    "date": comp["date"],
                    "day_type": comp["day_type"],
                    "title": comp["title"],
                    "comp_id": comp["id"],
                    "placement": pair["placement"],
                    "scratch_placement": pair.get("scratch_placement"),
                    "hcp_pct": pair["hcp_pct"],
                    "scratch_pct": pair["scratch_pct"],
                    "pair_hcp": pair["pair_hcp"],
                    "partner": partner.strip() if isinstance(partner, str) else partner,
                })

    players: list[dict] = []
    for _key, pd in player_data.items():
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

        votes: Counter = pd.pop("_name_votes", Counter())
        name = choose_display_name(votes)
        lk = normalize_player_key(name)

        bd = birth_by_key.get(lk)
        age = None
        if bd:
            try:
                birth = date.fromisoformat(bd)
                td = date.today()
                age = td.year - birth.year - ((td.month, td.day) < (birth.month, birth.day))
            except ValueError:
                pass

        individual_hcp = handicap_by_key.get(lk)

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
    print("Startar scraper – hämtar handicaplistor från SVB…", flush=True)
    birthdates = load_birthdates()
    results = fetch_all_results(birthdates)
    save_results(results)
    print(
        f"\nKlart! {results['meta']['total_competitions']} onsdagstävlingar, "
        f"{len(results['players'])} spelare."
    )
