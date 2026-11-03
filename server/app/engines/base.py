from __future__ import annotations
from pathlib import Path


class EngineError(RuntimeError):
    pass


class Engine:
    id = "base"
    label = ""
    reconstruction = True          # False = non deriva dalle foto (fixture)
    uploads_photos = False          # True = le foto lasciano il server
    destination = ""                # dove vanno le foto
    multiview = False
    cost_note = ""
    needs = ""                      # descrizione dei requisiti

    def availability(self) -> dict:
        return {"available": True, "missing": []}

    def describe(self) -> dict:
        a = self.availability()
        return {"id": self.id, "label": self.label, "reconstruction": self.reconstruction, "uploads_photos": self.uploads_photos,
                "destination": self.destination, "multiview": self.multiview, "cost_note": self.cost_note, "needs": self.needs, **a}

    def run(self, jd: Path, views: list[dict], options: dict, log, cancelled) -> dict:
        """Deve scrivere jd/raw/model.glb e ritornare {'usage': {...}}"""
        raise NotImplementedError


ROLE_PRIORITY = ["front", "back", "profile_left", "profile_right", "three_quarter"]


def pick_views(views: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    """Sceglie le viste del corpo da inviare al motore (in ordine di priorità) e restituisce (usate, non usate)."""
    en = [v for v in views if v.get("enabled", True)]
    body = sorted([v for v in en if v["role"] in ROLE_PRIORITY], key=lambda v: ROLE_PRIORITY.index(v["role"]))
    used = body[:limit]
    unused = [v for v in en if v not in used]
    return used, unused
