"""Job persistenti su disco + coda con un worker. Stato reale per fase, ripresa dalle fasi completate."""
from __future__ import annotations
import json, threading, queue, time, uuid, shutil, traceback
from pathlib import Path
from . import config, pipeline
from .engines import registry, EngineError

STAGES = ["reconstruct", "prepare", "rig", "export"]
STAGE_LABEL = {"reconstruct": "Generazione geometria e texture", "prepare": "Pulizia e preparazione mesh",
               "rig": "Scheletro, pesi e posa di riposo", "export": "Ottimizzazione, esportazione e validazione"}


def _now(): return time.time()


class JobStore:
    def __init__(self, root: Path = None):
        self.root = Path(root or config.JOBS_DIR); self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def dir(self, jid: str) -> Path:
        if not jid.replace("-", "").isalnum(): raise KeyError(jid)
        return self.root / jid

    def create(self, name: str) -> dict:
        jid = uuid.uuid4().hex[:10]
        d = self.dir(jid)
        for s in ("images", "views", "raw", "out"): (d / s).mkdir(parents=True, exist_ok=True)
        job = {"id": jid, "name": name or "personaggio", "created": _now(), "images": [], "views": [],
               "stages": {s: {"state": "pending", "message": "", "started": None, "finished": None} for s in STAGES},
               "options": {}, "engine": None, "provenance": None, "log": []}
        self.save(job); return job

    def get(self, jid: str) -> dict:
        p = self.dir(jid) / "job.json"
        if not p.exists(): raise KeyError(jid)
        with self.lock: return json.loads(p.read_text())

    def save(self, job: dict):
        with self.lock:
            p = self.dir(job["id"]) / "job.json"; tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1)); tmp.replace(p)

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                j = json.loads(p.read_text()); out.append({k: j[k] for k in ("id", "name", "created", "stages", "engine")})
            except Exception: pass
        return out

    def delete(self, jid: str):
        shutil.rmtree(self.dir(jid), ignore_errors=True)

    def update(self, jid: str, fn):
        with self.lock:
            j = self.get(jid); fn(j); self.save(j); return j


class Runner:
    def __init__(self, store: JobStore):
        self.store = store; self.q: queue.Queue = queue.Queue(); self.cancel: set[str] = set()
        self.engines = registry()
        self._recover()
        threading.Thread(target=self._loop, daemon=True, name="job-worker").start()

    def _recover(self):
        """Dopo un riavvio, le fasi rimaste 'running' diventano 'interrupted' e si possono riprendere."""
        for p in self.store.root.glob("*/job.json"):
            j = json.loads(p.read_text()); ch = False
            for s in j["stages"].values():
                if s["state"] in ("running", "queued"):
                    s["state"] = "interrupted"; s["message"] = "Interrotta da un riavvio del server: riprendi da questa fase."; ch = True
            if ch: self.store.save(j)

    def enqueue(self, jid: str, start: str, end: str, options: dict, engine: str | None, confirm_external: bool):
        a, b = STAGES.index(start), STAGES.index(end)
        if a > b: raise ValueError("Intervallo di fasi non valido")
        stages = STAGES[a:b + 1]
        def mut(j):
            running = [s for s, v in j["stages"].items() if v["state"] in ("queued", "running")]
            if running: raise RuntimeError(f"Il job ha già una fase in corso o in coda: {running[0]}")
            j["options"] = {**j.get("options", {}), **(options or {})}
            if "reconstruct" in stages:
                eng = self.engines.get(engine or "")
                if not eng: raise ValueError("Motore sconosciuto")
                av = eng.availability()
                if not av["available"]: raise ValueError("Motore non disponibile: " + "; ".join(av["missing"]))
                if eng.uploads_photos and not confirm_external:
                    raise PermissionError(f"Questo motore invia le foto a {eng.destination}. Serve la tua conferma esplicita.")
                j["engine"] = engine
                j["provenance"] = {"engine": engine, "reconstruction": eng.reconstruction, "uploads_photos": eng.uploads_photos,
                                   "destination": eng.destination if eng.uploads_photos else None}
            for i, s in enumerate(STAGES):
                if s in stages: j["stages"][s] = {"state": "queued", "message": "In coda", "started": None, "finished": None}
                elif STAGES.index(s) > a: j["stages"][s] = {"state": "pending", "message": "Da rieseguire", "started": None, "finished": None}
        self.store.update(jid, mut)
        self.cancel.discard(jid)
        self.q.put((jid, stages))

    def _log(self, jid: str, msg: str):
        def m(j):
            j["log"].append({"t": _now(), "m": msg}); j["log"] = j["log"][-300:]
        self.store.update(jid, m)

    def _set(self, jid, stage, state, msg=""):
        def m(j):
            s = j["stages"][stage]; s["state"] = state; s["message"] = msg
            if state == "running": s["started"] = _now()
            if state in ("done", "error", "cancelled"): s["finished"] = _now()
        self.store.update(jid, m)

    def _loop(self):
        while True:
            jid, stages = self.q.get()
            try:
                for st in stages:
                    if jid in self.cancel:
                        self._set(jid, st, "cancelled", "Annullata"); break
                    self._set(jid, st, "running", STAGE_LABEL[st])
                    try:
                        self._run_stage(jid, st)
                        self._set(jid, st, "done", "Completata")
                    except Exception as e:
                        msg = str(e) if isinstance(e, (EngineError, RuntimeError, ValueError)) else f"{type(e).__name__}: {e}"
                        self._log(jid, "ERRORE: " + msg + "\n" + "".join(traceback.format_exc().splitlines(True)[-3:]))
                        self._set(jid, st, "error", msg)
                        for s2 in stages[stages.index(st) + 1:]: self._set(jid, s2, "pending", "Non eseguita per l'errore precedente")
                        break
            finally:
                self.cancel.discard(jid)

    def _run_stage(self, jid: str, st: str):
        j = self.store.get(jid); jd = self.store.dir(jid); log = lambda m: self._log(jid, m)
        opts = j.get("options", {})
        if st == "reconstruct":
            eng = self.engines[j["engine"]]
            if not j["views"] and eng.reconstruction: raise RuntimeError("Nessuna vista assegnata (fase 2).")
            r = eng.run(jd, j["views"], opts, log, lambda: jid in self.cancel)
            self.store.update(jid, lambda jj: jj["provenance"].update({"usage": r["usage"]}))
            for n in r["usage"].get("notes", []): log(n)
        elif st == "prepare":
            pipeline.stage_prepare(jd, opts, log)
        elif st == "rig":
            pipeline.stage_rig(jd, opts, log)
        elif st == "export":
            name = "".join(c for c in j["name"].lower().replace(" ", "_") if c.isalnum() or c in "_-") or "avatar"
            rep = pipeline.stage_export(jd, name, opts, j.get("provenance"), log)
            if not rep["ready"]:
                # file non pronto: lo rinominiamo come bozza così non può essere scambiato per l'avatar finale
                src = jd / "out" / f"{name}.glb"; dst = jd / "out" / f"{name}_BOZZA.glb"
                if dst.exists(): dst.unlink()
                src.replace(dst); rep["file"] = dst.name
                (jd / "validation.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
                log("Validazione: BOZZA — " + " | ".join(rep["reasons"]))
            else:
                log("Validazione: avatar pronto")
