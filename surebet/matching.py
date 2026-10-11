"""
Emparejamiento del mismo partido entre fuentes distintas.

Reglas (las dos deben cumplirse):
  1. Mismo deporte y hora de inicio dentro de una ventana (default ±120 min).
  2. AMBOS equipos coinciden por nombre (directo o con local/visitante invertido).

Comparación de nombres:
  - Se normaliza (sin acentos, sin "FC/CF/UD/club...", alias de aliases.json).
  - Si los tokens de uno están contenidos en el otro -> 95 ("Dortmund" vs "Borussia Dortmund").
  - Si ambos tienen tokens distintos -> token_sort_ratio, que castiga
    "Manchester United" vs "Manchester City" (~62) en vez de darlo por bueno.
  - Marcadores de categoría (women, u21, ii, b...) deben coincidir o no hay match.
"""

from __future__ import annotations

import json
import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

from .models import MatchedEvent, SourceEvent

NOISE = frozenset("""
fc cf sc ac afc cd ud sd rc rcd ca club de del la le the calcio sk fk bk if sv
ssc as us ss vfl tsv fsv clube football futbol esporte
""".split())

TOKEN_ALIASES = {"utd": "united", "st": "saint", "ste": "sainte", "man": "manchester",
                 "intl": "international", "wom": "women", "fem": "women"}

DISTINGUISHING = frozenset({"women", "u17", "u18", "u19", "u20", "u21", "u22", "u23",
                            "ii", "iii", "b", "reserves", "youth", "academy"})

INDIVIDUAL_SPORTS = frozenset({"tennis", "mma", "boxing", "tabletennis", "darts"})

_ALIASES_FILE = Path(__file__).resolve().parent.parent / "aliases.json"


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _base_tokens(raw: str) -> list[str]:
    s = _strip_accents(raw).lower().replace("&", " and ")
    s = re.sub(r"\((w|women|f|fem)\)", " women ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = [TOKEN_ALIASES.get(t, t) for t in s.split()]
    kept = [t for t in toks if t not in NOISE and t != "and"]
    # Años de fundación y números sueltos ("Bologna FC 1909", "Schalke 04") salvo que sea lo único
    no_digits = [t for t in kept if not t.isdigit()]
    return no_digits or kept or toks


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    """variante normalizada -> canónico normalizado (desde aliases.json)."""
    if not _ALIASES_FILE.exists():
        return {}
    data = json.loads(_ALIASES_FILE.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for canonical, variants in data.items():
        if canonical.startswith("_"):
            continue
        canon = " ".join(_base_tokens(canonical))
        for v in [canonical, *variants]:
            out[" ".join(_base_tokens(v))] = canon
    return out


@lru_cache(maxsize=20000)
def normalize(raw: str) -> str:
    name = " ".join(_base_tokens(raw))
    return _aliases().get(name, name)


def _initials_ok(initials: set[str], other: set[str]) -> bool:
    firsts = {t[0] for t in other}
    return all(i in firsts for i in initials)


@lru_cache(maxsize=50000)
def name_score(a: str, b: str, sport: str = "") -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    ta, tb = set(na.split()), set(nb.split())
    if sport in INDIVIDUAL_SPORTS:
        # "B. Shelton" vs "Ben Shelton": las iniciales deben cuadrar con el nombre
        ia = {t for t in ta if len(t) == 1}
        ib = {t for t in tb if len(t) == 1}
        if (ia and not _initials_ok(ia, tb - ia)) or (ib and not _initials_ok(ib, ta - ib)):
            return 0.0
        ta, tb = (ta - ia) or ta, (tb - ib) or tb
    elif (ta ^ tb) & DISTINGUISHING:
        return 0.0
    if ta <= tb or tb <= ta:
        smaller = ta if len(ta) <= len(tb) else tb
        if sum(len(t) for t in smaller) >= 4:
            return 95.0
    return float(fuzz.token_sort_ratio(" ".join(sorted(ta)), " ".join(sorted(tb))))


# Puntaje de nombres por par de partidos (los nombres casi nunca cambian entre ciclos): sin esto el
# escáner recalculaba cientos de miles de comparaciones cada pocos segundos (la mayor parte de la CPU).
# Valor: puntaje*10 si es directo, -(puntaje*10)-1 si va invertido.
_PAIR_CACHE: dict[int, int] = {}
PAIR_CACHE_MAX = 150_000


@dataclass
class MatchConfig:
    threshold: float = 85.0
    time_window_min: float = 120.0


def event_score(a: SourceEvent, b: SourceEvent, cfg: MatchConfig) -> tuple[float, bool] | None:
    """(puntaje, invertido) o None si no pueden ser el mismo partido."""
    if a.sport != b.sport or a.sport.endswith(":other"):
        return None  # juegos/deportes sin identificar no se cruzan
    if a.start and b.start and abs((a.start - b.start).total_seconds()) > cfg.time_window_min * 60:
        return None
    s = a.sport
    key = hash((s, a.home, a.away, b.home, b.away))
    v = _PAIR_CACHE.get(key)
    if v is None:
        direct = min(name_score(a.home, b.home, s), name_score(a.away, b.away, s))
        swapped = min(name_score(a.home, b.away, s), name_score(a.away, b.home, s))
        v = round(direct * 10) if direct >= swapped else -round(swapped * 10) - 1
        if len(_PAIR_CACHE) >= PAIR_CACHE_MAX:
            _PAIR_CACHE.clear()
        _PAIR_CACHE[key] = v
    return (v / 10, False) if v >= 0 else ((-v - 1) / 10, True)


def match_events(events_by_feed: dict[str, list[SourceEvent]], cfg: MatchConfig) -> list[MatchedEvent]:
    """
    Agrupa partidos de todas las fuentes. La primera fuente (Polymarket) pone las anclas;
    cada evento de otra fuente se une al cluster con mejor puntaje (uno a uno, greedy).
    """
    clusters: list[MatchedEvent] = []
    window = cfg.time_window_min * 60
    for feed, events in events_by_feed.items():
        # Índice de anclas por deporte y hora: solo se comparan partidos del mismo deporte
        # que empiezan cerca (miles de partidos por fuente; comparar todos contra todos es lento)
        index: dict[str, tuple[list[float], list[int]]] = {}
        undated: dict[str, list[int]] = {}
        for ci, cl in enumerate(clusters):
            if feed in cl.feeds:
                continue
            if cl.anchor.start is None:
                undated.setdefault(cl.anchor.sport, []).append(ci)
            else:
                index.setdefault(cl.anchor.sport, ([], []))[1].append(ci)
        for sport, (times, cis) in index.items():
            cis.sort(key=lambda ci: clusters[ci].anchor.start.timestamp())
            times.extend(clusters[ci].anchor.start.timestamp() for ci in cis)

        candidates: list[tuple[float, int, int, bool]] = []
        for ei, ev in enumerate(events):
            near: list[int] = list(undated.get(ev.sport, []))
            times, cis = index.get(ev.sport, ([], []))
            if ev.start is None:
                near += cis
            elif times:
                t = ev.start.timestamp()
                near += cis[bisect_left(times, t - window): bisect_right(times, t + window)]
            for ci in near:
                res = event_score(clusters[ci].anchor, ev, cfg)
                if res and res[0] >= cfg.threshold:
                    candidates.append((res[0], ei, ci, res[1]))
        candidates.sort(key=lambda c: c[0], reverse=True)
        used_ev: set[int] = set()
        used_cl: set[int] = set()
        for _score, ei, ci, swapped in candidates:
            if ei in used_ev or ci in used_cl:
                continue
            clusters[ci].members.append((events[ei], swapped))
            used_ev.add(ei)
            used_cl.add(ci)
        for ei, ev in enumerate(events):
            if ei not in used_ev:
                clusters.append(MatchedEvent(anchor=ev, members=[(ev, False)]))
    return clusters
