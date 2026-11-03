"""Fasi di elaborazione locali (CPU): preparazione mesh, rigging+skinning, esportazione+validazione.
Ogni fase legge/scrive file nella cartella del job, così può essere ripresa dopo un riavvio."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from . import skeleton as sk, meshprep as mp, rigging as rg, validate as val
from .meshdata import MeshData, load_glb_mesh, vertex_normals
from .glbio import write_skinned_glb, write_static_glb

DEFAULTS = {
    "target_height": 1.67, "target_tris": 30000, "rest_pose": "A", "arm_angle_deg": 40.0, "leg_half_angle_deg": 5.0,
    "rotate_y_deg": 0.0, "auto_front": True, "bone_prefix": "", "max_texture": 2048,
}


def _opts(o: dict | None) -> dict:
    return {**DEFAULTS, **(o or {})}


def _wj(p: Path, d):
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False))


def stage_prepare(jd: Path, options: dict | None = None, log=lambda m: None) -> dict:
    o = _opts(options)
    raw = next((p for p in (jd / "raw").glob("model.*") if p.suffix.lower() in (".glb", ".gltf", ".obj")), None)
    if raw is None:
        raise RuntimeError("Nessun modello grezzo: esegui prima la ricostruzione (fase 4).")
    log("Lettura del modello del motore")
    md = load_glb_mesh(raw)
    rep: dict = {"input_tris": md.tris, "input_vertices": len(md.v)}
    log("Pulizia: pavimento, sfondo, frammenti")
    md, r = mp.clean(md); rep["clean"] = r
    if r.get("warning"):
        rep.setdefault("warnings", []).append(r["warning"])
    md, r = mp.detect_up_and_rotate(md); rep["orientation"] = r
    if o["rotate_y_deg"]:
        md = mp.rotate_y(md, float(o["rotate_y_deg"]))
    dz, face = mp.facing_estimate(md.v); rep["facing_estimate"] = {"dz": dz, "result": face}
    if o["auto_front"] and face == "-Z":
        md = mp.rotate_y(md, 180); rep["facing_estimate"]["applied"] = "ruotato di 180° attorno a Y"
    elif face == "indeterminato":
        rep.setdefault("warnings", []).append("Direzione frontale non determinabile dai piedi: verifica che il viso guardi +Z (usa 'Ruota 180°' se serve).")
    md, r = mp.place(md, float(o["target_height"])); rep["placement"] = r
    log("Decimazione con preservazione delle UV")
    md, r = mp.decimate(md, int(o["target_tris"])); rep["decimation"] = r
    md.save(jd / "prepared")
    if md.tex is not None:
        write_static_glb(jd / "preview_prepared.glb", md.v, md.normals(), md.uv, md.f, md.tex)
    rep["tris"] = md.tris; rep["vertices"] = len(md.v)
    _wj(jd / "prepare_report.json", rep)
    return rep


def stage_rig(jd: Path, options: dict | None = None, log=lambda m: None) -> dict:
    o = _opts(options)
    md = MeshData.load(jd / "prepared")
    rep: dict = {}
    log("Stima dei giunti dalla mesh")
    J, info = rg.estimate_joints(md.v)
    J = rg.symmetrize(J)
    est = {k: [float(x) for x in J[k]] for k in sk.NAMES}
    ov_path = jd / "joint_overrides.json"
    overrides = json.loads(ov_path.read_text()) if ov_path.exists() else {}
    for k, val_ in overrides.items():
        if k in J: J[k] = np.array(val_, float)
    rep["estimate"] = info; rep["overrides"] = list(overrides)
    _wj(jd / "joints_estimated.json", est)
    Ja = sk.joints_array(J)
    log("Calcolo dei pesi (distanza geodesica di superficie)")
    jidx, w = rg.compute_weights(md.v, md.f, Ja)
    _wj(jd / "joints_source.json", {k: [float(x) for x in J[k]] for k in sk.NAMES})
    log(f"Conversione alla posa di riposo {o['rest_pose']}")
    v2, J2, pinfo = rg.convert_pose(md.v, jidx, w.astype(float), Ja, target=o["rest_pose"],
                                    arm_angle_deg=float(o["arm_angle_deg"]), leg_half_angle_deg=float(o["leg_half_angle_deg"]))
    rep["pose"] = pinfo
    rep["stretch_after_pose"] = rg.edge_stretch(md.v, v2, md.f)
    warns = list(info.get("warnings", []))
    if rep["stretch_after_pose"]["max"] > 3.0:
        warns.append(f"La conversione di posa stira alcuni lati ×{rep['stretch_after_pose']['max']:.1f}: braccia/gambe probabilmente fuse col corpo o giunti sbagliati.")
    rep["warnings"] = warns
    np.savez_compressed(jd / "rig.npz", v=v2, f=md.f, uv=md.uv, J=J2, jidx=jidx, w=w)
    if md.tex is not None: md.tex.save(jd / "rig.png")
    _wj(jd / "joints_rest.json", {n: [float(x) for x in J2[i]] for i, n in enumerate(sk.NAMES)})
    _wj(jd / "rig_report.json", rep)
    return rep


def stage_export(jd: Path, name: str, options: dict | None = None, provenance: dict | None = None, log=lambda m: None) -> dict:
    from PIL import Image
    o = _opts(options)
    z = np.load(jd / "rig.npz")
    v, f, uv, J, jidx, w = z["v"].copy(), z["f"], z["uv"], z["J"].copy(), z["jidx"], z["w"]
    tex = Image.open(jd / "rig.png"); tex.load()
    # scala modificabile: l'altezza di export può differire da quella di lavoro
    cur = float(v[:, 1].max() - v[:, 1].min()); s = float(o["target_height"]) / cur
    v *= s; J *= s
    n = vertex_normals(v, f)
    out = jd / "out"; out.mkdir(exist_ok=True)
    for old in out.glob("*.glb"): old.unlink()
    glb = out / f"{name}.glb"
    log("Scrittura del GLB")
    ex = {"provenance": provenance or {}, "rest_pose": o["rest_pose"], "units": "m", "axes": "+Y up, +Z front"}
    winfo = write_skinned_glb(glb, v, n, uv, f, J, jidx, w, tex, prefix=o["bone_prefix"], max_tex=int(o["max_texture"]), name=name, extras=ex)
    log("Validazione del file esportato")
    rep = val.validate_glb(glb, expect_height=float(o["target_height"]), provenance=provenance)
    rep["export"] = winfo; rep["file"] = glb.name
    _wj(jd / "validation.json", rep)
    return rep
