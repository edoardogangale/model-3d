"""Meshy (servizio REMOTO A PAGAMENTO a crediti): image-to-3D e multi-image-to-3D.

NON COLLAUDATO in questo repository: nell'ambiente di sviluppo mancavano chiave API e accesso di rete a api.meshy.ai.
Gli endpoint seguono la documentazione pubblica di Meshy (https://docs.meshy.ai) ma vanno verificati al primo uso reale.
Le foto vengono INVIATE a api.meshy.ai: la UI richiede una conferma esplicita.
"""
from __future__ import annotations
import base64, io, time
from pathlib import Path
import httpx
from PIL import Image
from .. import config
from .base import Engine, EngineError, pick_views


def _data_uri(p: Path) -> str:
    im = Image.open(p)
    im.thumbnail((1536, 1536))
    bio = io.BytesIO(); im.save(bio, "PNG")
    return "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode()


class MeshyEngine(Engine):
    id = "meshy"
    label = "Meshy (servizio remoto a crediti)"
    uploads_photos = True
    destination = "api.meshy.ai (Meshy Inc.)"
    multiview = True
    cost_note = "Consuma crediti del tuo account Meshy (testo + texture). Controlla il listino prima di avviare: nessun acquisto viene attivato dall'app."
    needs = "Variabile d'ambiente MESHY_API_KEY sul server e rete verso api.meshy.ai."

    def availability(self):
        miss = [] if config.MESHY_API_KEY else ["MESHY_API_KEY non impostata"]
        return {"available": not miss, "missing": miss}

    def run(self, jd: Path, views, options, log, cancelled) -> dict:
        used, unused = pick_views(views, 4)
        if not used:
            raise EngineError("Servono almeno una vista del corpo (fronte, retro, profilo o tre quarti).")
        hdr = {"Authorization": f"Bearer {config.MESHY_API_KEY}"}
        body = {"should_texture": True, "enable_pbr": False, "pose_mode": options.get("pose_mode", "a-pose"),
                "target_polycount": int(options.get("target_polycount", 30000)), "topology": "triangle", "should_remesh": True}
        if len(used) == 1:
            ep = "/openapi/v1/image-to-3d"; body["image_url"] = _data_uri(Path(jd / used[0]["file"]))
        else:
            ep = "/openapi/v1/multi-image-to-3d"; body["image_urls"] = [_data_uri(Path(jd / v["file"])) for v in used]
        with httpx.Client(base_url=config.MESHY_BASE, headers=hdr, timeout=120) as c:
            log(f"Invio {len(used)} vista/e a {config.MESHY_BASE}{ep}")
            r = c.post(ep, json=body)
            if r.status_code >= 300:
                raise EngineError(f"Meshy ha rifiutato la richiesta ({r.status_code}): {r.text[:300]}")
            tid = r.json().get("result")
            if not tid:
                raise EngineError(f"Risposta Meshy inattesa: {r.text[:300]}")
            (jd / "raw").mkdir(exist_ok=True)
            while True:
                if cancelled(): raise EngineError("Annullato dall'utente (il task Meshy può restare attivo sul loro server).")
                time.sleep(5)
                s = c.get(f"{ep}/{tid}")
                if s.status_code >= 300:
                    raise EngineError(f"Errore nel controllo dello stato Meshy ({s.status_code}): {s.text[:200]}")
                j = s.json(); st = j.get("status")
                log(f"Meshy: {st} ({j.get('progress', 0)}% riportato dal servizio)")
                if st == "SUCCEEDED": break
                if st in ("FAILED", "CANCELED"):
                    raise EngineError(f"Meshy ha terminato con stato {st}: {(j.get('task_error') or {}).get('message', '')}")
            url = (j.get("model_urls") or {}).get("glb")
            if not url:
                raise EngineError("Meshy non ha restituito un GLB.")
            g = httpx.get(url, timeout=300, follow_redirects=True); g.raise_for_status()
            (jd / "raw" / "model.glb").write_bytes(g.content)
        notes = []
        if len(used) == 1:
            notes.append("Una sola vista inviata: ricostruzione da singola foto (le parti non visibili sono inventate dal modello).")
        else:
            notes.append(f"Ricostruzione multivista con {len(used)} immagini (Meshy accetta al massimo 4).")
        if unused: notes.append("Viste non inviate al motore (restano di riferimento visivo): " + ", ".join(v["role"] for v in unused))
        return {"usage": {"used": [v["id"] for v in used], "unused": [v["id"] for v in unused], "notes": notes, "task_id": tid}}
