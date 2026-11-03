"""Validazione del GLB ESPORTATO (rilegge i byte del file). Tre categorie:
 - structural: errori verificabili automaticamente (bloccano il download come "avatar pronto")
 - compat: compatibilità con le animazioni (da provare col tuo gioco)
 - visual: qualità/somiglianza (solo anteprima umana; qui solo indizi)
"""
from __future__ import annotations
import json, subprocess, shutil, math
from pathlib import Path
import numpy as np
from . import skeleton as sk
from .glbio import GLB
from .rigging import edge_stretch

LIMITS = {"tris_target": 30000, "tris_max": 50000, "tex_max": 2048, "materials_max": 3}


def _chk(out, cat, id_, ok, msg, severity="error", value=None):
    out.append({"category": cat, "id": id_, "ok": bool(ok), "severity": "info" if ok else severity, "message": msg, "value": value})


def poses(J: np.ndarray) -> dict[str, dict[str, np.ndarray]]:
    """Pose procedurali per provare il rig (rotazioni locali nel frame del genitore; a riposo i frame sono allineati al mondo)."""
    R = sk.rot_axis_angle
    def toward(d, tgt, ang):  # ruota la direzione d verso tgt
        return R(np.cross(d, tgt), ang)
    P: dict[str, dict[str, np.ndarray]] = {}
    arm, el, kn, st, wk = {}, {}, {}, {}, {}
    for side, s in (("Left", 1), ("Right", -1)):
        d = J[sk.IDX[f"{side}ForeArm"]] - J[sk.IDX[f"{side}Arm"]]; d = d / np.linalg.norm(d)
        arm[f"{side}Arm"] = R([0, 0, 1], s * np.deg2rad(60))               # braccia sollevate lateralmente
        df = J[sk.IDX[f"{side}Hand"]] - J[sk.IDX[f"{side}ForeArm"]]; df = df / np.linalg.norm(df)
        el[f"{side}ForeArm"] = toward(df, np.array([0, 0, 1.0]), np.deg2rad(100))   # gomiti piegati in avanti
        dl = np.array([0, -1.0, 0])
        kn[f"{side}Leg"] = toward(dl, np.array([0, 0, -1.0]), np.deg2rad(70))       # ginocchia piegate
        kn[f"{side}UpLeg"] = toward(dl, np.array([0, 0, 1.0]), np.deg2rad(35))
    st = {"Spine": R([0, 1, 0], np.deg2rad(15)), "Spine1": R([0, 1, 0], np.deg2rad(15)), "Spine2": R([0, 1, 0], np.deg2rad(10))}
    wk = {"LeftUpLeg": toward(np.array([0, -1.0, 0]), np.array([0, 0, 1.0]), np.deg2rad(28)),
          "RightUpLeg": toward(np.array([0, -1.0, 0]), np.array([0, 0, -1.0]), np.deg2rad(22)),
          "RightLeg": toward(np.array([0, -1.0, 0]), np.array([0, 0, -1.0]), np.deg2rad(30)),
          "LeftArm": R([1, 0, 0], np.deg2rad(-20)), "RightArm": R([1, 0, 0], np.deg2rad(20))}
    P["braccia_sollevate"] = arm; P["gomiti_piegati"] = el; P["ginocchia_piegate"] = kn
    P["torsione_busto"] = st; P["passo"] = wk
    return P


def validate_glb(path: str | Path, *, expect_height: float | None = 1.67, provenance: dict | None = None,
                 node_validator: bool = True) -> dict:
    checks: list[dict] = []
    path = Path(path)
    try:
        g = GLB(path)
    except Exception as e:
        _chk(checks, "structural", "container", False, f"File GLB illeggibile: {e}")
        return _summary(checks, {}, provenance)
    j = g.json
    stats: dict = {"file_bytes": g.size}
    _chk(checks, "structural", "container", True, "Contenitore GLB valido (JSON + BIN)")
    ext = [i for i in j.get("images", []) if "uri" in i] + [b for b in j.get("buffers", []) if "uri" in b]
    _chk(checks, "structural", "no_external", not ext, "Nessuna risorsa esterna (buffer e immagini incorporati)" if not ext else "Risorse esterne trovate")
    skins = j.get("skins", [])
    nodes = j.get("nodes", [])
    skinned = [n for n in nodes if "mesh" in n and "skin" in n]
    _chk(checks, "structural", "skinned_mesh", bool(skinned and skins), "Presente almeno una SkinnedMesh collegata a uno scheletro" if skinned else "Nessuna mesh con skin: il file è STATICO")
    if not (skinned and skins):
        return _summary(checks, stats, provenance)
    skin = skins[skinned[0]["skin"]]
    joints = skin["joints"]
    jn = [nodes[i].get("name", "") for i in joints]
    stats["bones"] = len(joints)
    # --- geometria
    mesh = j["meshes"][skinned[0]["mesh"]]
    tris = 0; V = []; pr_list = mesh["primitives"]
    for pr in pr_list:
        at = pr["attributes"]
        pos = g.accessor(at["POSITION"]); V.append(pos)
        idx = g.accessor(pr["indices"]).reshape(-1).astype(int) if "indices" in pr else np.arange(len(pos))
        tris += len(idx) // 3
    P = np.concatenate(V)
    pr = pr_list[0]; at = pr["attributes"]
    pos = g.accessor(at["POSITION"]); idx = g.accessor(pr["indices"]).reshape(-1).astype(int); F = idx.reshape(-1, 3)
    stats["triangles"] = int(tris); stats["vertices"] = int(len(P))
    _chk(checks, "structural", "tri_limit", tris <= LIMITS["tris_max"], f"Triangoli: {tris} (obiettivo {LIMITS['tris_target']}, max {LIMITS['tris_max']})", value=tris)
    if tris > LIMITS["tris_target"] and tris <= LIMITS["tris_max"]:
        _chk(checks, "structural", "tri_target", False, f"Sopra l'obiettivo di {LIMITS['tris_target']} ma entro il massimo", "warning")
    allfinite = all(np.isfinite(g.accessor(a)).all() for a in range(len(j["accessors"])) if j["accessors"][a]["componentType"] == 5126)
    _chk(checks, "structural", "finite", allfinite, "Nessun valore non finito (NaN/Inf) negli accessor")
    _chk(checks, "structural", "index_range", idx.max() < len(pos) and idx.min() >= 0, "Indici dei triangoli nel range dei vertici")
    e1 = pos[F[:, 1]] - pos[F[:, 0]]; e2 = pos[F[:, 2]] - pos[F[:, 0]]
    area = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)
    degen = int((area < 1e-12).sum())
    _chk(checks, "structural", "degenerate_tris", degen <= max(5, 0.001 * len(F)), f"Triangoli degeneri: {degen}", "warning", degen)
    nor = g.accessor(at["NORMAL"]) if "NORMAL" in at else None
    _chk(checks, "structural", "normals", nor is not None and abs(np.linalg.norm(nor, axis=1) - 1).max() < 1e-2,
         "Normali presenti e unitarie" if nor is not None else "Normali mancanti")
    uv = g.accessor(at["TEXCOORD_0"]) if "TEXCOORD_0" in at else None
    uv_ok = uv is not None and np.isfinite(uv).all() and uv.min() >= -1e-3 and uv.max() <= 1 + 1e-3
    _chk(checks, "structural", "uv", uv_ok, "UV presenti, finite e dentro [0,1]" if uv_ok else "UV mancanti o fuori dall'intervallo [0,1]")
    # --- trasformazioni dei nodi
    bad_t = 0
    for n in nodes:
        if "matrix" in n: bad_t += 1
        for k in ("translation", "rotation", "scale"):
            if k in n and not np.isfinite(n[k]).all(): bad_t += 1
        if "scale" in n and min(abs(x) for x in n["scale"]) < 1e-6: bad_t += 1
    _chk(checks, "structural", "transforms", bad_t == 0, "Trasformazioni dei nodi finite e non degeneri" if bad_t == 0 else f"{bad_t} trasformazioni anomale")
    # --- materiali / texture
    mats = j.get("materials", []); stats["materials"] = len(mats)
    _chk(checks, "structural", "materials", 1 <= len(mats) <= LIMITS["materials_max"], f"Materiali: {len(mats)} (1–{LIMITS['materials_max']})")
    texs = []
    for i in range(len(j.get("images", []))):
        try:
            im = g.image(i); im.load(); texs.append(list(im.size))
        except Exception as e:
            _chk(checks, "structural", "texture_decode", False, f"Texture {i} non decodificabile: {e}")
    stats["textures"] = texs
    _chk(checks, "structural", "texture_size", bool(texs) and all(max(t) <= LIMITS["tex_max"] for t in texs),
         f"Texture: {texs} (max {LIMITS['tex_max']} px per lato)")
    # --- scheletro
    joint_set = set(joints)
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []): parent[c] = i
    # radice dello scheletro
    names = [sk.strip_prefix(n) for n in jn]
    stats["bone_prefix"] = "mixamorig" if any(n.startswith("mixamorig") for n in jn) else ""
    missing = [n for n in sk.REQUIRED_MIXAMO if n not in names]
    _chk(checks, "compat", "mixamo_names", not missing, "Tutte le ossa del rig Mixamo presenti" if not missing else f"Ossa mancanti: {missing}")
    name2node = {sk.strip_prefix(nodes[i].get("name", "")): i for i in joints}
    wrong = []
    for n, p in sk.BONES:
        if n in name2node and p:
            par = parent.get(name2node[n])
            if par is None or sk.strip_prefix(nodes[par].get("name", "")) != p: wrong.append(f"{n}→{p}")
    _chk(checks, "compat", "mixamo_hierarchy", not wrong, "Gerarchia coerente con Mixamo" if not wrong else f"Genitori errati: {wrong}")
    # --- skinning
    jt = g.accessor(at["JOINTS_0"]).astype(int); wt = g.accessor(at["WEIGHTS_0"])
    _chk(checks, "structural", "joint_index", jt.max() < len(joints), "Indici JOINTS_0 validi rispetto allo skin")
    _chk(checks, "structural", "max4", "JOINTS_1" not in at, "Massimo 4 influenze per vertice")
    s = wt.sum(1)
    _chk(checks, "structural", "weights_norm", np.abs(s - 1).max() < 1e-3, f"Pesi normalizzati (scarto max {np.abs(s-1).max():.2e})")
    _chk(checks, "structural", "no_unweighted", (s > 0.5).all(), f"Nessun vertice senza pesi (min somma {s.min():.3f})", value=float(s.min()))
    used = np.unique(jt[wt > 1e-6]); stats["bones_with_weights"] = int(len(used))
    _chk(checks, "structural", "bones_used", len(used) >= 15, f"Ossa con pesi effettivi: {len(used)}/{len(joints)}", "error")
    # --- IBM e bind pose
    ibm = g.accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    G = {}
    def world(i):
        if i in G: return G[i]
        n = nodes[i]; M = np.eye(4)
        if "translation" in n: M[:3, 3] = n["translation"]
        if "rotation" in n:
            x, y, z, w = n["rotation"]
            M[:3, :3] = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)], [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
        if "scale" in n: M[:3, :3] = M[:3, :3] @ np.diag(n["scale"])
        G[i] = (world(parent[i]) @ M) if i in parent else M
        return G[i]
    err = max(np.abs(world(joints[k]) @ ibm[k] - np.eye(4)).max() for k in range(len(joints)))
    mesh_world = world(next(i for i, n in enumerate(nodes) if "skin" in n))
    _chk(checks, "structural", "ibm", err < 1e-4 and np.allclose(mesh_world, np.eye(4), atol=1e-6),
         f"Inverse bind matrices coerenti con la posa di riposo (errore {err:.1e}); mesh senza trasformazione propria")
    Jw = np.stack([world(i)[:3, 3] for i in joints])
    # --- dimensioni/orientamento
    H = float(pos[:, 1].max() - pos[:, 1].min()); stats["height_m"] = H; stats["ymin"] = float(pos[:, 1].min())
    stats["bbox"] = [[float(x) for x in pos.min(0)], [float(x) for x in pos.max(0)]]
    _chk(checks, "structural", "grounded", abs(pos[:, 1].min()) < 0.01, f"Piedi sul piano y=0 (ymin={pos[:,1].min():.4f} m)")
    if expect_height:
        _chk(checks, "structural", "height", abs(H - expect_height) < 0.02, f"Altezza {H:.3f} m (attesa {expect_height} m)", "warning", H)
    ni = {sk.strip_prefix(nodes[i].get("name", "")): world(i)[:3, 3] for i in joints}
    ok_lr = ni["LeftArm"][0] > ni["RightArm"][0] and ni["LeftUpLeg"][0] > ni["RightUpLeg"][0]
    _chk(checks, "structural", "left_plus_x", ok_lr, "Sinistra anatomica a +X (personaggio rivolto a +Z)")
    ok_up = ni["Head"][1] > ni["Hips"][1] > ni["LeftFoot"][1]
    _chk(checks, "structural", "up_plus_y", ok_up, "Asse verticale +Y: testa sopra bacino sopra piedi")
    toe_f = ni["LeftToeBase"][2] - ni["LeftFoot"][2]
    _chk(checks, "structural", "front_plus_z", toe_f > 0, "Punte dei piedi verso +Z (fronte +Z)")
    arm_ang = float(np.rad2deg(np.arccos(np.clip(-(ni["LeftHand"] - ni["LeftArm"])[1] / np.linalg.norm(ni["LeftHand"] - ni["LeftArm"]), -1, 1))))
    stats["arm_angle_from_down_deg"] = arm_ang
    pose = "T" if arm_ang > 75 else "A" if arm_ang > 25 else "braccia lungo i fianchi"
    _chk(checks, "compat", "rest_pose", pose in ("A", "T"), f"Posa di riposo: {pose} (angolo braccia {arm_ang:.0f}° dalla verticale)", "warning")
    # --- deformazione reale a partire dai dati del file
    Jr = np.stack([ni[n] for n in sk.NAMES]) if all(n in ni for n in sk.NAMES) else None
    if Jr is not None and (stats["bone_prefix"] is not None):
        # rimappa jt (indici nel file) → indici nell'ordine NAMES
        order = [sk.IDX.get(n, -1) for n in names]
        jt_n = np.array(order)[jt]
        mx = {}
        base_ok = True
        for pname, rot in poses(Jr).items():
            Gp = sk.fk_matrices(Jr, rot); sm = Gp @ sk.ibm_from_rest(Jr)
            vp = sk.skin_vertices(pos, jt_n, wt, sm)
            st = edge_stretch(pos, vp, F)
            disp = np.linalg.norm(vp - pos, axis=1)
            mx[pname] = {"max_disp_m": float(disp.max()), "edge_p999": st["p999"], "edge_max": st["max"], "finite": bool(np.isfinite(vp).all())}
        stats["deform"] = mx
        fin = all(m["finite"] for m in mx.values())
        moved = all(m["max_disp_m"] > 0.03 for m in mx.values())
        _chk(checks, "structural", "deform_finite", fin, "Le pose di prova producono vertici finiti")
        _chk(checks, "structural", "deform_moves", moved, "Muovendo le ossa la mesh si deforma davvero (spostamento > 3 cm in ogni prova)")
        worst = max(m["edge_p999"] for m in mx.values()); worstmax = max(m["edge_max"] for m in mx.values())
        _chk(checks, "structural", "deform_explosion", worstmax < 6.0, f"Nessuna esplosione: allungamento massimo dei lati ×{worstmax:.2f}", "error", worstmax)
        _chk(checks, "visual", "deform_stretch", worst < 2.0, f"Allungamento al 99.9° percentile ×{worst:.2f} (da controllare a vista su spalle/anche/ginocchia)", "warning", worst)
    # --- validatore Khronos
    if node_validator:
        r = khronos_validate(path)
        stats["khronos"] = r
        if r.get("available"):
            _chk(checks, "structural", "khronos", r["errors"] == 0, f"gltf-validator (Khronos): {r['errors']} errori, {r['warnings']} avvisi", value=r)
        else:
            _chk(checks, "structural", "khronos", False, f"gltf-validator non eseguito: {r.get('reason')}", "warning")
    _chk(checks, "visual", "visual_review", False, "Somiglianza, vestiti e capelli: controlla nell'anteprima a 360° e a confronto con le foto (non verificabile automaticamente)", "info")
    _chk(checks, "compat", "anim_compat", False, "Compatibilità con le animazioni del gioco: va provata caricando una tua animazione di riferimento (non si può dedurre dai soli nomi)", "info")
    return _summary(checks, stats, provenance)


def khronos_validate(path: Path) -> dict:
    node = shutil.which("node")
    script = Path(__file__).resolve().parents[1] / "tools" / "gltf_validate.mjs"
    if not node or not script.exists():
        return {"available": False, "reason": "node o tools/gltf_validate.mjs mancanti"}
    try:
        p = subprocess.run([node, str(script), str(path)], capture_output=True, text=True, timeout=120)
        if p.returncode != 0 and not p.stdout.strip():
            return {"available": False, "reason": (p.stderr or "errore sconosciuto")[:300]}
        r = json.loads(p.stdout); r["available"] = True; return r
    except Exception as e:
        return {"available": False, "reason": str(e)[:300]}


def _summary(checks, stats, provenance) -> dict:
    structural_fail = [c for c in checks if c["category"] == "structural" and not c["ok"] and c["severity"] == "error"]
    prov_ok = bool(provenance and provenance.get("reconstruction"))
    verdict = "ready" if (not structural_fail and prov_ok) else "draft"
    reasons = [c["message"] for c in structural_fail]
    if not prov_ok:
        reasons.append("L'avatar non deriva da una ricostruzione fotografica reale (motore di prova/manichino): resta una BOZZA.")
    return {"verdict": verdict, "ready": verdict == "ready", "reasons": reasons, "checks": checks, "stats": stats, "provenance": provenance or {}}
