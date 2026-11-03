"""Motore locale/remoto tramite comando configurabile (es. TRELLIS.2 su un PC con GPU, anche via ssh).

Placeholder nel comando: {image} (vista frontale PNG), {images_dir} (cartella con tutte le viste), {out} (GLB da scrivere).
NON COLLAUDATO qui: l'ambiente di sviluppo non ha GPU NVIDIA né accesso ai pesi su Hugging Face.
"""
from __future__ import annotations
import shlex, subprocess, shutil
from pathlib import Path
from .. import config
from .base import Engine, EngineError, pick_views


class LocalCommandEngine(Engine):
    id = "local_cmd"
    label = "Motore locale (TRELLIS.2 o altro, via comando)"
    multiview = False
    uploads_photos = False
    destination = "Il comando configurato (può essere un altro PC raggiunto via ssh)"
    cost_note = "Nessun costo di servizio; richiede la tua GPU."
    needs = ("MODEL3D_LOCAL_CMD con {image} e {out}. Per TRELLIS.2 (MIT): Linux, GPU NVIDIA ≥ 24 GB, CUDA 12.4; "
             "usa tools/trellis2_worker.py. TRELLIS.2 accetta UNA sola immagine: si usa la vista frontale.")

    def availability(self):
        miss = [] if config.LOCAL_CMD else ["MODEL3D_LOCAL_CMD non impostata"]
        return {"available": not miss, "missing": miss}

    def run(self, jd: Path, views, options, log, cancelled) -> dict:
        used, unused = pick_views(views, 1)
        if not used:
            raise EngineError("Serve almeno una vista del corpo.")
        (jd / "raw").mkdir(exist_ok=True)
        out = jd / "raw" / "model.glb"
        cmd = config.LOCAL_CMD.format(image=shlex.quote(str(jd / used[0]["file"])), images_dir=shlex.quote(str(jd / "views")),
                                     out=shlex.quote(str(out)))
        log(f"Eseguo: {cmd[:200]}")
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        import time
        t0 = time.time()
        try:
            for line in p.stdout:
                log(line.rstrip()[:300])
                if cancelled(): p.kill(); raise EngineError("Annullato dall'utente")
                if time.time() - t0 > config.LOCAL_CMD_TIMEOUT: p.kill(); raise EngineError("Timeout del comando del motore")
        finally:
            p.wait()
        if p.returncode != 0:
            raise EngineError(f"Il comando del motore è terminato con codice {p.returncode} (vedi log).")
        if not out.exists():
            raise EngineError(f"Il comando è terminato ma non ha scritto il GLB atteso in {out}.")
        return {"usage": {"used": [used[0]["id"]], "unused": [v["id"] for v in unused],
                          "notes": ["Motore a singola immagine: usata solo la vista " + used[0]["role"] + ". Le altre viste NON hanno influenzato la geometria."]}}
