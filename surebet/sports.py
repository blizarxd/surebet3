"""Mapeo de deportes de cada fuente a una familia común (soccer, basketball...)."""

from __future__ import annotations

import re

# IDs de tags de Polymarket (gamma-api.polymarket.com/tags/<id>)
POLY_TAG_FAMILY = {
    "100350": "soccer",
    "28": "basketball", "745": "basketball", "100149": "basketball", "101178": "basketball",
    "100254": "basketball", "102669": "basketball",
    "450": "americanfootball", "100351": "americanfootball",
    "100381": "baseball", "678": "baseball",
    "899": "hockey", "100088": "hockey",
    "864": "tennis",
    "279": "mma", "683": "boxing",
    "64": "esports",
    "517": "cricket",
    "102883": "volleyball",
    "102897": "handball",
    "102193": "rugby",
    "103767": "tabletennis",
    "102393": "lacrosse",
    "100219": "golf",
}

# Tags genéricos que no dicen el deporte (Sports, Games)
POLY_GENERIC_TAGS = {"1", "100639"}

ODDSAPI_PREFIX_FAMILY = {
    "soccer_": "soccer",
    "basketball_": "basketball",
    "americanfootball_": "americanfootball",
    "baseball_": "baseball",
    "icehockey_": "hockey",
    "tennis_": "tennis",
    "mma_": "mma",
    "boxing_": "boxing",
    "cricket_": "cricket",
    "rugbyleague_": "rugby",
    "rugbyunion_": "rugby",
    "aussierules_": "aussierules",
    "lacrosse_": "lacrosse",
    "handball_": "handball",
    "golf_": "golf",
}


def poly_family_from_tags(tag_ids: list[str], sport_code: str = "") -> str:
    for t in tag_ids:
        if t in POLY_TAG_FAMILY:
            fam = POLY_TAG_FAMILY[t]
            return esports_family(sport_code, sport_code) if fam == "esports" else fam
    return f"other:{sport_code}" if sport_code else "other"


def oddsapi_family(sport_key: str) -> str:
    for prefix, fam in ODDSAPI_PREFIX_FAMILY.items():
        if sport_key.startswith(prefix):
            return fam
    return f"other:{sport_key}"


# Esports: cada juego es un "deporte" distinto. FURIA o LOS tienen equipos en CS2 y en R6:
# sin esto, "FURIA vs LOS" de un juego se cruzaba con el del otro (falso +29%).
ESPORT_CODES = {
    "cs2": "cs2", "csgo": "cs2", "lol": "lol", "dota2": "dota2", "val": "valorant", "r6siege": "r6",
    "rl": "rl", "ow": "ow", "sc": "sc", "sc1": "sc", "sc2": "sc", "codmw": "cod", "hok": "hok",
    "mlbb": "mlbb", "fifa": "fifa", "lol-wild-rift": "wildrift", "pubg": "pubg",
}
ESPORT_NAME_PATTERNS = [
    (r"wild rift", "wildrift"), (r"\bcs ?2\b|counter strike|\bcsgo\b", "cs2"), (r"rainbow six|\br6\b", "r6"),
    (r"league of legends|\blol\b", "lol"), (r"dota", "dota2"), (r"valorant", "valorant"),
    (r"mobile legends", "mlbb"), (r"overwatch", "ow"), (r"starcraft", "sc"), (r"honor of kings", "hok"),
    (r"call of duty", "cod"), (r"rocket league", "rl"), (r"\bfifa\b|ea sports fc", "fifa"), (r"pubg", "pubg"),
]


def esports_family(code: str = "", name: str = "") -> str:
    """'esports:<juego>' a partir del código de Polymarket o del nombre de la liga/serie."""
    if code in ESPORT_CODES:
        return f"esports:{ESPORT_CODES[code]}"
    text = name.lower().replace("-", " ")
    for pattern, game in ESPORT_NAME_PATTERNS:
        if re.search(pattern, text):
            return f"esports:{game}"
    return "esports:other"
