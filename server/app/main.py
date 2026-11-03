from __future__ import annotations
import hmac, json, shutil, uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import config, images as imgs
from .jobs import JobStore, Runner, STAGES, STAGE_LABEL
from .pipeline import DEFAULTS

app = FastAPI(title="Model 3D — da foto ad avatar GLB")
store = JobStore()
runner = Runner(store)


@app.middleware("http")
async def auth(request: Request, call_next):
    if config.TOKEN and request.url.path.startswith("/api") and request.url.path not in ("/api/login", "/api/health"):
        tok = request.cookies.get("m3d_token") or request.headers.get("x-token", "")
        if not hmac.compare_digest(tok, config.TOKEN):
            return JSONResponse({"detail": "Token richiesto"}, status_code=401)
    return await call_next(request)


class Login(BaseModel):
    token: str


@app.post("/api/login")
def login(b: Login, response: Response):
    if config.TOKEN and not hmac.compare_digest(b.token, config.TOKEN):
        raise HTTPException(401, "Token errato")
    response.set_cookie("m3d_token", b.token, httponly=True, samesite="lax", max_age=86400 * 30)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "auth_required": bool(config.TOKEN)}


@app.get("/api/capabilities")
def capabilities():
    return {"engines": [e.describe() for e in runner.engines.values()], "roles": imgs.ROLES, "defaults": DEFAULTS,
            "stages": [{"id": s, "label": STAGE_LABEL[s]} for s in STAGES]}


def _job(jid: str) -> dict:
    try: return store.get(jid)
    except KeyError: raise HTTPException(404, "Job non trovato")


@app.get("/api/jobs")
def list_jobs(): return store.list()


class NewJob(BaseModel):
    name: str = "babu"


@app.post("/api/jobs")
def new_job(b: NewJob): return store.create(b.name)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = _job(jid); d = store.dir(jid)
    j["files"] = {k: (d / k).exists() for k in ("raw/model.glb", "prepared.npz", "rig.npz", "validation.json", "prepare_report.json", "rig_report.json",
                                                "joints_estimated.json", "joints_source.json", "joints_rest.json", "joint_overrides.json", "preview_prepared.glb")}
    j["outputs"] = sorted(p.name for p in (d / "out").glob("*.glb"))
    return j


@app.delete("/api/jobs/{jid}")
def del_job(jid: str):
    _job(jid); store.delete(jid); return {"ok": True}


@app.post("/api/jobs/{jid}/images")
async def upload(jid: str, files: list[UploadFile] = File(...)):
    j = _job(jid); d = store.dir(jid); added = []
    for f in files:
        data = await f.read()
        if len(data) > 40 * 1024 * 1024: raise HTTPException(413, f"{f.filename}: oltre 40 MB")
        iid = uuid.uuid4().hex[:8]; ext = Path(f.filename or "x.png").suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".heic"): ext = ".png"
        p = d / "images" / f"{iid}{ext}"; p.write_bytes(data)
        try:
            im = imgs.open_image(p); im.save(d / "images" / f"{iid}.png"); size = im.size
            if p.suffix != ".png": p.unlink()
        except Exception as e:
            p.unlink(missing_ok=True); raise HTTPException(400, f"{f.filename}: immagine non leggibile ({e})")
        added.append({"id": iid, "name": f.filename, "file": f"images/{iid}.png", "size": list(size)})
    store.update(jid, lambda jj: jj["images"].extend(added))
    return added


class ViewIn(BaseModel):
    id: str | None = None
    image_id: str
    crop: list[float]
    exclude: list[list[float]] = []
    role: str
    label: str = ""
    remove_bg: bool = True
    tol: float = 28.0
    enabled: bool = True


@app.put("/api/jobs/{jid}/views")
def put_views(jid: str, views: list[ViewIn]):
    j = _job(jid); d = store.dir(jid); out = []
    imgmap = {i["id"]: i for i in j["images"]}
    for v in views:
        if v.role not in imgs.ROLES: raise HTTPException(400, f"Ruolo sconosciuto: {v.role}")
        if v.image_id not in imgmap: raise HTTPException(400, "Immagine sconosciuta")
        vid = v.id or uuid.uuid4().hex[:8]
        info = imgs.make_view(d / imgmap[v.image_id]["file"], v.crop, v.exclude, v.remove_bg, v.tol, d / "views" / f"{vid}.png", d / "views" / f"{vid}_t.png")
        out.append({**v.model_dump(), "id": vid, "file": f"views/{vid}.png", "thumb": f"views/{vid}_t.png", **info})
    store.update(jid, lambda jj: jj.__setitem__("views", out))
    return out


class RunIn(BaseModel):
    start: str = "reconstruct"
    end: str = "export"
    engine: str | None = None
    options: dict = {}
    confirm_external: bool = False


@app.post("/api/jobs/{jid}/run")
def run(jid: str, b: RunIn):
    _job(jid)
    if b.start not in STAGES or b.end not in STAGES: raise HTTPException(400, "Fase sconosciuta")
    try:
        runner.enqueue(jid, b.start, b.end, b.options, b.engine, b.confirm_external)
    except PermissionError as e: raise HTTPException(412, str(e))
    except (ValueError, RuntimeError) as e: raise HTTPException(409, str(e))
    return get_job(jid)


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    _job(jid); runner.cancel.add(jid); return {"ok": True}


@app.post("/api/jobs/{jid}/import-model")
async def import_model(jid: str, file: UploadFile = File(...)):
    """Usa un GLB generato altrove (es. TRELLIS.2 su un altro PC o un servizio web) al posto del motore integrato."""
    j = _job(jid); d = store.dir(jid)
    data = await file.read()
    if data[:4] != b"glTF": raise HTTPException(400, "Non è un file GLB")
    (d / "raw").mkdir(exist_ok=True); (d / "raw" / "model.glb").write_bytes(data)
    def m(jj):
        jj["engine"] = "import"
        jj["provenance"] = {"engine": "import", "reconstruction": True, "declared_by_user": True, "uploads_photos": False,
                            "usage": {"notes": ["Modello importato dall'utente: l'app non può verificare da quali foto/motore derivi."]}}
        jj["stages"]["reconstruct"] = {"state": "done", "message": "Modello importato", "started": None, "finished": None}
        for s in STAGES[1:]: jj["stages"][s] = {"state": "pending", "message": "Da eseguire", "started": None, "finished": None}
    store.update(jid, m); return get_job(jid)


class Joints(BaseModel):
    overrides: dict[str, list[float]]


@app.put("/api/jobs/{jid}/joints")
def put_joints(jid: str, b: Joints):
    from . import skeleton as sk
    _job(jid)
    for k, v in b.overrides.items():
        if k not in sk.IDX or len(v) != 3: raise HTTPException(400, f"Giunto non valido: {k}")
    (store.dir(jid) / "joint_overrides.json").write_text(json.dumps(b.overrides)); return {"ok": True}


@app.delete("/api/jobs/{jid}/joints")
def del_joints(jid: str):
    _job(jid); (store.dir(jid) / "joint_overrides.json").unlink(missing_ok=True); return {"ok": True}


SERVE = {"raw/model.glb", "preview_prepared.glb", "prepare_report.json", "rig_report.json", "joints_estimated.json", "joints_source.json",
         "joints_rest.json", "joint_overrides.json", "validation.json"}


@app.get("/api/jobs/{jid}/file/{path:path}")
def file(jid: str, path: str):
    _job(jid); d = store.dir(jid)
    if path in SERVE or path.startswith(("views/", "images/")):
        p = (d / path).resolve()
        if d.resolve() in p.parents and p.exists(): return FileResponse(p)
    raise HTTPException(404, "File non disponibile")


def _out(jid: str, draft: bool) -> Path:
    j = _job(jid); d = store.dir(jid)
    vp = d / "validation.json"
    if not vp.exists(): raise HTTPException(409, "Nessun GLB esportato: completa la fase 8 (esportazione e validazione).")
    rep = json.loads(vp.read_text()); p = d / "out" / rep["file"]
    if not p.exists(): raise HTTPException(404, "File non trovato")
    if not draft and not rep["ready"]:
        raise HTTPException(409, "Il file NON è un avatar pronto: " + " | ".join(rep["reasons"]) + " Puoi scaricare la bozza separatamente.")
    return p


@app.get("/api/jobs/{jid}/download")
def download(jid: str):
    p = _out(jid, False); return FileResponse(p, media_type="model/gltf-binary", filename=p.name)


@app.get("/api/jobs/{jid}/download-draft")
def download_draft(jid: str):
    p = _out(jid, True)
    name = p.name if "_BOZZA" in p.name else p.stem + "_BOZZA.glb"
    return FileResponse(p, media_type="model/gltf-binary", filename=name)


@app.get("/api/jobs/{jid}/glb")
def glb_for_viewer(jid: str):
    """Il GLB candidato (anche bozza) per l'anteprima nel viewer; non è un download di 'avatar pronto'."""
    return FileResponse(_out(jid, True), media_type="model/gltf-binary")


if config.WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=config.WEB_DIST, html=True), name="web")
