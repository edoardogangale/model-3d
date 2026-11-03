"""Preparazione mesh: pulizia di sfondo/pavimento/frammenti, orientamento, scala, decimazione con UV."""
from __future__ import annotations
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from .meshdata import MeshData


def weld_index(v: np.ndarray, tol: float = 1e-6) -> np.ndarray:
    key = np.round(v / max(tol, 1e-9)).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1)


def face_components(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    w = weld_index(v, 1e-5 * max(np.ptp(v, axis=0).max(), 1e-6))
    fw = w[f]
    n = w.max() + 1
    r = np.concatenate([fw[:, 0], fw[:, 1], fw[:, 2]]); c = np.concatenate([fw[:, 1], fw[:, 2], fw[:, 0]])
    g = coo_matrix((np.ones(len(r)), (r, c)), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    return lab[fw[:, 0]]


def tri_areas(v, f):
    return 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)


def compact(md: MeshData, keep_faces: np.ndarray) -> MeshData:
    f = md.f[keep_faces]
    used = np.unique(f)
    remap = -np.ones(len(md.v), int); remap[used] = np.arange(len(used))
    return MeshData(md.v[used], remap[f], md.uv[used], md.tex, dict(md.info))


def clean(md: MeshData, min_part_ratio: float = 0.03) -> tuple[MeshData, dict]:
    """Rimuove pavimento/lastre, sfondo e frammenti flottanti. Restituisce un report leggibile."""
    rep = {"removed_floor_slabs": 0, "removed_fragments": 0, "removed_degenerate": 0, "components_before": 0}
    area = tri_areas(md.v, md.f)
    deg = area < 1e-14
    rep["removed_degenerate"] = int(deg.sum())
    keep = ~deg
    md = compact(md, keep) if deg.any() else md
    lab = face_components(md.v, md.f)
    area = tri_areas(md.v, md.f)
    ids = np.unique(lab); rep["components_before"] = int(len(ids))
    tot = np.array([area[lab == i].sum() for i in ids])
    # candidato "personaggio": il componente più esteso che NON sia una lastra sottile (pavimento/sfondo/bassorilievo)
    exts = {i: np.ptp(md.v[md.f[lab == i]].reshape(-1, 3), axis=0) for i in ids}
    slab = {i: bool(exts[i].min() < 0.03 * exts[i].max()) for i in ids}
    cand = [k for k, i in enumerate(ids) if not slab[i]]
    if not cand:
        rep["warning"] = "Tutti i componenti sono lastre sottili: il motore ha prodotto un bassorilievo/piano, non un volume."
        cand = list(range(len(ids)))
    main = ids[max(cand, key=lambda k: tot[k])]
    keep_faces = np.zeros(len(md.f), bool)
    main_ext = exts[main]
    for i, a in zip(ids, tot):
        fi = lab == i
        ext = exts[i]
        if i != main and slab[i] and ext.max() > 0.2 * main_ext.max():
            rep["removed_floor_slabs"] += 1; continue
        if i != main and a < min_part_ratio * tot[ids == main][0]:
            rep["removed_fragments"] += 1; continue
        keep_faces |= fi
    # lastra/pavimento FUSO col corpo: piatto sottile sotto i piedi, più largo del personaggio
    out = compact(md, keep_faces)
    return out, rep


def detect_up_and_rotate(md: MeshData) -> tuple[MeshData, dict]:
    ext = np.ptp(md.v, axis=0)
    ax = int(ext.argmax()); note = "Y già verticale"
    v = md.v.copy()
    if ax != 1:
        # porta l'asse più lungo su +Y
        perm = {0: lambda p: np.stack([-p[:, 1], p[:, 0], p[:, 2]], 1), 2: lambda p: np.stack([p[:, 0], p[:, 2], -p[:, 1]], 1)}[ax]
        v = perm(v); note = f"asse più alto era {'XYZ'[ax]}: ruotato su +Y"
    md2 = MeshData(v, md.f, md.uv, md.tex, dict(md.info))
    return md2, {"up_axis": note}


def rotate_y(md: MeshData, deg: float) -> MeshData:
    a = np.deg2rad(deg); c, s = np.cos(a), np.sin(a)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return MeshData(md.v @ R.T, md.f, md.uv, md.tex, dict(md.info))


def facing_estimate(v: np.ndarray) -> tuple[float, str]:
    """Stima la direzione frontale dai piedi: le punte sono più avanti delle caviglie. Ritorna (dz, esito)."""
    H = np.ptp(v[:, 1]); y0 = v[:, 1].min()
    sole = v[v[:, 1] < y0 + 0.02 * H]
    ankle = v[(v[:, 1] > y0 + 0.06 * H) & (v[:, 1] < y0 + 0.10 * H)]
    if len(sole) < 20 or len(ankle) < 20:
        return 0.0, "indeterminato"
    # la punta si estende più in avanti che indietro rispetto alla caviglia
    zc = ankle[:, 2].mean()
    fwd = sole[:, 2].max() - zc; back = zc - sole[:, 2].min()
    dz = float(fwd - back)
    return dz, ("+Z" if dz > 0.01 * H else "-Z" if dz < -0.01 * H else "indeterminato")


def place(md: MeshData, height: float) -> tuple[MeshData, dict]:
    v = md.v.copy()
    H = np.ptp(v[:, 1]); s = height / H
    v *= s
    v[:, 1] -= v[:, 1].min()
    # origine tra i piedi: x al centro dell'ingombro dei piedi, z sulla verticale delle caviglie (non sulle punte)
    feet = v[v[:, 1] < 0.10 * height]
    ank = v[(v[:, 1] > 0.05 * height) & (v[:, 1] < 0.10 * height)]
    v[:, 0] -= 0.5 * (feet[:, 0].min() + feet[:, 0].max()) if len(feet) else v[:, 0].mean()
    v[:, 2] -= ank[:, 2].mean() if len(ank) else v[:, 2].mean()
    return MeshData(v, md.f, md.uv, md.tex, dict(md.info)), {"scale_applied": float(s), "height_m": float(height)}


def decimate(md: MeshData, target_tris: int) -> tuple[MeshData, dict]:
    """Decimazione quadrica meshoptimizer che preserva le UV (attributi) e penalizza lo schiacciamento dei bordi di UV."""
    if md.tris <= target_tris:
        return md, {"decimated": False, "tris_before": md.tris, "tris_after": md.tris}
    import ctypes
    from meshoptimizer import simplifier as _mo
    lib = _mo.lib
    v = np.ascontiguousarray(md.v, dtype=np.float32); uv = np.ascontiguousarray(md.uv, dtype=np.float32)
    idx = np.ascontiguousarray(md.f.reshape(-1), dtype=np.uint32)
    dst = np.zeros_like(idx)
    wts = np.array([1.0, 1.0], dtype=np.float32)
    err = ctypes.c_float(0.0)
    P = lambda a, t: a.ctypes.data_as(ctypes.POINTER(t))
    lib.meshopt_simplifyWithAttributes.restype = ctypes.c_size_t
    lib.meshopt_simplifyWithAttributes.argtypes = [
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t, ctypes.c_float, ctypes.c_uint, ctypes.POINTER(ctypes.c_float)]
    n = lib.meshopt_simplifyWithAttributes(P(dst, ctypes.c_uint), P(idx, ctypes.c_uint), len(idx), P(v, ctypes.c_float), len(v), 12,
                                           P(uv, ctypes.c_float), 8, P(wts, ctypes.c_float), 2, None,
                                           int(target_tris * 3), 0.05, 0, ctypes.byref(err))
    f2 = dst[:n].reshape(-1, 3).astype(int)
    out = compact(MeshData(md.v, f2, md.uv, md.tex), np.arange(len(f2)))
    return out, {"decimated": True, "tris_before": md.tris, "tris_after": out.tris, "simplify_error": float(err.value)}
