from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from ..models import SourceEvent


class Source(ABC):
    """Una fuente de cuotas. `fetch` devuelve los partidos con sus quotes actuales."""

    name: str = ""
    slow: bool = False  # True si sus cuotas no son tiempo real (se avisa en vivo)

    def __init__(self) -> None:
        self.status = "sin datos aún"

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> list[SourceEvent]:
        ...


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    if " " in v and "T" not in v:
        v = v.replace(" ", "T", 1)
    if v.endswith("+00"):
        v += ":00"
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
