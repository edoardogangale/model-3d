"""Stima dei giunti, calcolo dei pesi di skinning, conversione di posa coerente con bind pose e IBM."""
from __future__ import annotations
import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import dijkstra
from . import skeleton as sk
from .meshdata import MeshData
from .meshprep import weld_index


# ---------------------------------------------------------------- stima giunti
def _side_clusters_gap(xs: np.ndarray, gap: float, start: float) -> float:
    """Dal centro verso l'esterno: ritorna la x del bordo del torso (primo vuoto > gap oltre `start`)."""
    xs = np.sort(xs[xs > 0])
    prev = xs[0] if len(xs) else 0
    for x in xs:
        if x - prev > gap and prev > start:
            return float(prev)
        prev = x
    return float(prev)


def estimate_joints(v: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
    """Stima euristica per un umano in piedi, +Y su, fronte +Z, piedi a y=0, centrato.
    Parte da proporzioni anatomiche e le corregge con le sezioni della mesh. NON è un autorigger:
    serve braccia/gambe separate; in caso contrario segnala il problema e il giunto va corretto a mano."""
    H = float(v[:, 1].max())
    warn: list[str] = []
    info: dict = {"height": H}

    def band(y0, y1, mask=None):
        m = (v[:, 1] >= y0 * H) & (v[:, 1] <= y1 * H)
        if mask is not None: m &= mask
        return v[m]

    # centro del tronco (esclude braccia: |x| piccolo)
    tor = band(0.55, 0.72, np.abs(v[:, 0]) < 0.14 * H)
    feet = v[v[:, 1] < 0.10 * H]
    cx = float(0.5 * (feet[:, 0].min() + feet[:, 0].max())) if len(feet) else 0.0
    cz = float(np.median(tor[:, 2])) if len(tor) else 0.0

    # gambe
    leg_y = {"knee": 0.285, "ankle": 0.045, "hip": 0.50}
    J: dict[str, np.ndarray] = {}
    for side, s in (("Left", 1), ("Right", -1)):
        m = s * (v[:, 0] - cx) > 0.01 * H
        up = band(0.34, 0.44, m & (np.abs(v[:, 0] - cx) < 0.17 * H))
        kn = band(0.25, 0.32, m & (np.abs(v[:, 0] - cx) < 0.17 * H))
        an = band(0.03, 0.07, m & (np.abs(v[:, 0] - cx) < 0.17 * H))
        if min(len(up), len(kn), len(an)) < 10:
            warn.append(f"gamba {side}: sezioni vuote, uso proporzioni")
            up = kn = an = np.array([[cx + s * 0.09 * H, 0, cz]])
        hx = s * abs(up[:, 0].mean() - cx) * 0.98 + cx
        J[f"{side}UpLeg"] = np.array([hx, leg_y["hip"] * H, up[:, 2].mean() if len(up) else cz])
        J[f"{side}Leg"] = np.array([kn[:, 0].mean(), leg_y["knee"] * H, kn[:, 2].mean()])
        J[f"{side}Foot"] = np.array([an[:, 0].mean(), leg_y["ankle"] * H, an[:, 2].mean()])
        toe = v[(v[:, 1] < 0.02 * H) & m]
        tz = float(toe[:, 2].max()) if len(toe) else J[f"{side}Foot"][2] + 0.1 * H
        J[f"{side}ToeBase"] = np.array([J[f"{side}Foot"][0], 0.012 * H, J[f"{side}Foot"][2] + 0.6 * (tz - J[f"{side}Foot"][2])])
        J[f"{side}UpLeg"][1] = 0.50 * H

    # asse
    J["Hips"] = np.array([cx, 0.53 * H, cz])
    J["Spine"] = np.array([cx, 0.585 * H, cz])
    J["Spine1"] = np.array([cx, 0.66 * H, cz])
    J["Spine2"] = np.array([cx, 0.735 * H, cz])
    J["Neck"] = np.array([cx, 0.82 * H, cz])
    head = band(0.88, 1.0, np.abs(v[:, 0] - cx) < 0.12 * H)
    hz = float(np.median(head[:, 2])) if len(head) else cz
    J["Head"] = np.array([cx, 0.875 * H, hz])
    J["HeadTop_End"] = np.array([cx, H, hz])

    # braccia: bordo del torso in vita, poi punti oltre il bordo
    waist = band(0.56, 0.62)
    torso_half = _side_clusters_gap(np.abs(waist[:, 0] - cx), 0.012 * H, 0.04 * H) if len(waist) else 0.1 * H
    info["torso_half_width"] = torso_half
    sh_y = 0.815 * H
    for side, s in (("Left", 1), ("Right", -1)):
        m = (s * (v[:, 0] - cx) > torso_half + 0.004 * H) & (v[:, 1] > 0.40 * H) & (v[:, 1] < sh_y + 0.03 * H)
        # esclude le gambe/le mani fuse con la coscia: sopra l'inguine o fuori dalla gamba
        leg_out = abs(J[f"{side}UpLeg"][0] - cx) + 0.04 * H
        m &= ~((v[:, 1] < 0.47 * H) & (np.abs(v[:, 0] - cx) < leg_out))
        pts = v[m]
        shoulder = np.array([cx + s * 0.115 * H, 0.82 * H, cz])
        J[f"{side}Shoulder"] = np.array([cx + s * 0.035 * H, 0.82 * H, cz])
        if len(pts) < 50:
            warn.append(f"braccio {side}: non separabile dal busto — correggi spalla/gomito/polso a mano")
            ang = np.deg2rad(8)
            d = np.array([s * np.sin(ang), -np.cos(ang), 0])
            L = 0.315 * H
            info[f"arm_{side}_points"] = int(len(pts))
        else:
            # direzione principale con raffinamento iterativo (scarta capelli/spalla fuori asse)
            sel = pts
            for _ in range(3):
                c = sel.mean(0)
                u, sv, vt = np.linalg.svd(sel - c, full_matrices=False)
                d = vt[0]
                if d[0] * s < 0 and abs(d[0]) > abs(d[1]): d = -d
                if d[1] > 0 and abs(d[1]) > abs(d[0]): d = -d
                rel = pts - shoulder
                t = rel @ d
                rad = np.linalg.norm(rel - np.outer(t, d), axis=1)
                sel = pts[(rad < 0.07 * H) & (t > -0.03 * H)]
                if len(sel) < 30: sel = pts; break
            rel = sel - shoulder; t = rel @ d
            L = float(np.percentile(t, 99.9))
            info[f"arm_{side}_points"] = int(len(sel))
            info[f"arm_{side}_dir"] = [float(x) for x in d]
        L = float(np.clip(L, 0.28 * H, 0.46 * H))
        J[f"{side}Arm"] = shoulder   # proporzioni antropometriche: braccio .42, avambraccio .33, mano .25 della lunghezza spalla-dita
        J[f"{side}ForeArm"] = shoulder + d * 0.42 * L
        J[f"{side}Hand"] = shoulder + d * 0.75 * L
        info[f"arm_{side}_length"] = L
    return J, {"warnings": warn, **info}


def symmetrize(J: dict[str, np.ndarray], cx: float | None = None) -> dict[str, np.ndarray]:
    cx = float(J["Hips"][0]) if cx is None else cx
    out = {k: np.array(x, float) for k, x in J.items()}
    for n in list(out):
        if n.startswith("Left"):
            r = "Right" + n[4:]
            a, b = out[n], out[r]
            m = np.array([(abs(a[0] - cx) + abs(b[0] - cx)) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2])
            out[n] = np.array([cx + m[0], m[1], m[2]]); out[r] = np.array([cx - m[0], m[1], m[2]])
    for n in ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "HeadTop_End"):
        out[n][0] = cx
    return out


# ---------------------------------------------------------------- pesi di skinning
def _graph(v: np.ndarray, f: np.ndarray):
    w = weld_index(v, 1e-5 * max(np.ptp(v, axis=0).max(), 1e-6))
    n = w.max() + 1
    fw = w[f]
    a = np.concatenate([fw[:, 0], fw[:, 1], fw[:, 2]]); b = np.concatenate([fw[:, 1], fw[:, 2], fw[:, 0]])
    pos = np.zeros((n, 3)); np.add.at(pos, w, v); cnt = np.bincount(w, minlength=n)[:, None]; pos /= cnt
    d = np.linalg.norm(pos[a] - pos[b], axis=1)
    g = coo_matrix((d + 1e-9, (a, b)), shape=(n, n)).tocsr()
    g = g.maximum(g.T)
    return w, pos, g


def _seg_dist(P: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a; L2 = float(ab @ ab) + 1e-12
    t = np.clip(((P - a) @ ab) / L2, 0, 1)
    return np.linalg.norm(P - (a + np.outer(t, ab)), axis=1)


def compute_weights(v: np.ndarray, f: np.ndarray, J: np.ndarray, power: float = 3.0, smooth_iters: int = 6):
    """Pesi basati su distanza geodesica di superficie dalle ossa (evita che un braccio si 'attacchi' al busto
    solo per vicinanza euclidea), potenza inversa, 4 influenze max, normalizzati, lisciati sulla mesh."""
    H = float(np.ptp(v[:, 1]))
    wi, pos, g = _graph(v, f)
    seg = sk.bone_segments(J, H)
    names = sk.WEIGHTED
    n = len(pos)
    D = np.full((n, len(names)), np.inf)
    for k, nm in enumerate(names):
        a, b = seg[nm]
        de = _seg_dist(pos, a, b)
        dmin = de.min()
        seeds = np.where(de <= dmin * 1.35 + 0.004 * H)[0]
        dg = dijkstra(g, directed=False, indices=seeds, min_only=True)
        dg = np.where(np.isfinite(dg), dg, np.inf)
        D[:, k] = dg + dmin
        bad = ~np.isfinite(D[:, k])
        if bad.any():            # parti scollegate (capelli/accessori): fallback euclideo penalizzato
            D[bad, k] = de[bad] * 3 + 0.02 * H
    D = np.maximum(D, 1e-3 * H)
    W = 1.0 / D ** power
    # 4 influenze
    top = np.argsort(-W, axis=1)[:, :4]
    M = np.zeros_like(W); np.put_along_axis(M, top, np.take_along_axis(W, top, 1), 1)
    M /= M.sum(1, keepdims=True)
    # lisciatura laplaciana sul grafo saldato
    A = g.copy(); A.data[:] = 1.0
    deg = np.asarray(A.sum(1)).ravel(); deg[deg == 0] = 1
    Pn = csr_matrix(A.multiply(1.0 / deg[:, None]))
    for _ in range(smooth_iters):
        M = 0.5 * M + 0.5 * (Pn @ M)
        top = np.argsort(-M, axis=1)[:, :4]
        K = np.zeros_like(M); np.put_along_axis(K, top, np.take_along_axis(M, top, 1), 1)
        M = K / K.sum(1, keepdims=True)
    top = np.argsort(-M, axis=1)[:, :4]
    wt = np.take_along_axis(M, top, 1)
    wt /= wt.sum(1, keepdims=True)
    jmap = np.array([sk.IDX[nm] for nm in names])
    jidx = jmap[top]
    # per-vertice originale (le UV duplicano i vertici sulle cuciture: stessa posizione = stessi pesi)
    return jidx[wi].astype(np.uint16), wt[wi].astype(np.float32)


# ---------------------------------------------------------------- conversione di posa
def convert_pose(v: np.ndarray, jidx: np.ndarray, w: np.ndarray, J: np.ndarray, *,
                 target: str = "A", arm_angle_deg: float = 40.0, leg_half_angle_deg: float = 5.0):
    """Porta la mesh dalla posa sorgente alla posa di riposo (A/T) usando i pesi già calcolati.
    Ritorna (v', J') con J' = nuovo scheletro di riposo: bind pose e IBM verranno derivate da J'."""
    if target == "keep":
        return v.copy(), J.copy(), {"converted": False}
    ang = np.deg2rad(90.0 if target == "T" else arm_angle_deg)
    Jn = J.copy()
    M = np.tile(np.eye(4), (len(sk.NAMES), 1, 1))   # trasformazione rigida di ciascun osso: x -> Rx + t
    info = {"converted": True, "target": target}
    for side, s in (("Left", 1), ("Right", -1)):
        a, fa, h = J[sk.IDX[f"{side}Arm"]], J[sk.IDX[f"{side}ForeArm"]], J[sk.IDX[f"{side}Hand"]]
        tdir = np.array([s * np.sin(ang), -np.cos(ang), 0.0])
        R1 = sk.rot_between(fa - a, tdir)
        fa2 = a + R1 @ (fa - a); h2 = a + R1 @ (h - a)
        R2 = sk.rot_between(h2 - fa2, tdir)           # distende l'avambraccio
        h3 = fa2 + R2 @ (h2 - fa2)
        T1 = _rigid(R1, a)
        T2 = _rigid(R2, fa2)
        M[sk.IDX[f"{side}Arm"]] = T1
        M[sk.IDX[f"{side}ForeArm"]] = T2 @ T1
        M[sk.IDX[f"{side}Hand"]] = T2 @ T1
        Jn[sk.IDX[f"{side}ForeArm"]] = fa2; Jn[sk.IDX[f"{side}Hand"]] = h3
        info[f"{side}_arm_rotation_deg"] = float(np.rad2deg(np.arccos(np.clip(((fa - a) / np.linalg.norm(fa - a)) @ tdir, -1, 1))))
    la = np.deg2rad(leg_half_angle_deg)
    for side, s in (("Left", 1), ("Right", -1)):
        hip, kn, an, toe = (J[sk.IDX[f"{side}{x}"]] for x in ("UpLeg", "Leg", "Foot", "ToeBase"))
        d_cur = an - hip
        tdir = np.array([s * np.sin(la), -np.cos(la), 0.0])
        # angolo attuale sul piano coronale, ruota attorno all'asse z passando dal fianco
        cur = np.arctan2(s * d_cur[0], -d_cur[1])
        R = sk.rot_axis_angle([0, 0, 1], -s * (la - cur))
        # controllo segno: la direzione risultante deve avvicinarsi a tdir
        if np.linalg.norm((R @ d_cur) / np.linalg.norm(d_cur) - tdir) > np.linalg.norm(d_cur / np.linalg.norm(d_cur) - tdir):
            R = R.T
        T = _rigid(R, hip)
        for n in (f"{side}UpLeg", f"{side}Leg"):
            M[sk.IDX[n]] = T
        # piede e punta: seguono la posizione ma mantengono l'orientamento (suola piatta)
        an2 = hip + R @ (an - hip)
        Tf = _rigid(np.eye(3), an)
        Tf[:3, 3] = an2 - an
        M[sk.IDX[f"{side}Foot"]] = Tf; M[sk.IDX[f"{side}ToeBase"]] = Tf
        Jn[sk.IDX[f"{side}Leg"]] = hip + R @ (kn - hip); Jn[sk.IDX[f"{side}Foot"]] = an2
        Jn[sk.IDX[f"{side}ToeBase"]] = toe + (an2 - an)
    Mw = M[jidx]                                    # (N,4,4,4)
    vh = np.concatenate([v, np.ones((len(v), 1))], 1)
    out = np.zeros_like(v)
    for k in range(4):
        out += w[:, k:k + 1] * np.einsum("nij,nj->ni", Mw[:, k, :3, :], vh)
    # piedi a terra
    dy = out[:, 1].min()
    out[:, 1] -= dy; Jn[:, 1] -= dy
    info["ground_shift"] = float(dy)
    return out, Jn, info


def _rigid(R: np.ndarray, center: np.ndarray) -> np.ndarray:
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = center - R @ center
    return T


def edge_stretch(v0: np.ndarray, v1: np.ndarray, f: np.ndarray) -> dict:
    """Rapporto di lunghezza dei lati prima/dopo. Regolarizzato con 0.25×lato mediano per non farsi ingannare da lati quasi nulli."""
    e = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    l0 = np.linalg.norm(v0[e[:, 0]] - v0[e[:, 1]], axis=1); l1 = np.linalg.norm(v1[e[:, 0]] - v1[e[:, 1]], axis=1)
    eps = 0.25 * float(np.median(l0))
    r = (l1 + eps) / (l0 + eps)
    return {"max": float(r.max()), "p999": float(np.percentile(r, 99.9)), "min": float(r.min())}
