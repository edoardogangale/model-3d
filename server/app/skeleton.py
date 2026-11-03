"""Scheletro umanoide compatibile con la gerarchia Mixamo + utilità di forward kinematics.

Convenzioni: Y su, il personaggio guarda verso +Z, quindi la SUA sinistra è a +X.
Tutte le ossa hanno rotazione locale identità nella posa di riposo: la posa è definita
solo dalle posizioni dei giunti (bind pose coerente con le inverse bind matrices).
"""
from __future__ import annotations
import numpy as np

# (nome, genitore) — l'ordine è quello dei joint nello skin
BONES: list[tuple[str, str | None]] = [
    ("Hips", None),
    ("Spine", "Hips"), ("Spine1", "Spine"), ("Spine2", "Spine1"),
    ("Neck", "Spine2"), ("Head", "Neck"), ("HeadTop_End", "Head"),
    ("LeftShoulder", "Spine2"), ("LeftArm", "LeftShoulder"),
    ("LeftForeArm", "LeftArm"), ("LeftHand", "LeftForeArm"),
    ("RightShoulder", "Spine2"), ("RightArm", "RightShoulder"),
    ("RightForeArm", "RightArm"), ("RightHand", "RightForeArm"),
    ("LeftUpLeg", "Hips"), ("LeftLeg", "LeftUpLeg"), ("LeftFoot", "LeftLeg"), ("LeftToeBase", "LeftFoot"),
    ("RightUpLeg", "Hips"), ("RightLeg", "RightUpLeg"), ("RightFoot", "RightLeg"), ("RightToeBase", "RightFoot"),
]
NAMES = [b for b, _ in BONES]
IDX = {n: i for i, n in enumerate(NAMES)}
PARENT = np.array([IDX[p] if p else -1 for _, p in BONES], dtype=np.int32)
# Ossa che non muovono nulla (solo punta): HeadTop_End
LEAF_ONLY = {"HeadTop_End"}
WEIGHTED = [n for n in NAMES if n not in LEAF_ONLY]
REQUIRED_MIXAMO = [n for n in NAMES if n not in ("HeadTop_End", "LeftToeBase", "RightToeBase")]


def bone_name(n: str, prefix: str = "") -> str:
    return f"{prefix}{n}" if prefix else n


def strip_prefix(n: str) -> str:
    for p in ("mixamorig:", "mixamorig"):
        if n.startswith(p):
            return n[len(p):]
    return n


def children(name: str) -> list[str]:
    return [b for b, p in BONES if p == name]


def joints_array(d: dict[str, np.ndarray]) -> np.ndarray:
    return np.stack([np.asarray(d[n], float) for n in NAMES])


def bone_segments(J: np.ndarray, H: float) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Segmento (origine, punta) usato per calcolare i pesi di ogni osso."""
    seg: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for n in WEIGHTED:
        a = J[IDX[n]]
        ch = [c for c in children(n)]
        if n in ("Spine2",):
            ch = ["Neck"]  # la clavicola è un osso a parte
        if n == "Hips":
            ch = ["Spine"]
        if ch:
            b = np.mean([J[IDX[c]] for c in ch], axis=0)
        elif n.endswith("Hand"):
            d = a - J[IDX[n.replace("Hand", "ForeArm")]]
            b = a + d / (np.linalg.norm(d) + 1e-9) * 0.09 * H
        else:  # ToeBase
            b = a + np.array([0, 0, 0.05 * H])
        seg[n] = (a, b)
    seg["Head"] = (J[IDX["Head"]], J[IDX["HeadTop_End"]])
    return seg


def rot_axis_angle(axis: np.ndarray, ang: float) -> np.ndarray:
    a = np.asarray(axis, float)
    n = np.linalg.norm(a)
    if n < 1e-12 or abs(ang) < 1e-12:
        return np.eye(3)
    a = a / n
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K


def rot_between(d0: np.ndarray, d1: np.ndarray) -> np.ndarray:
    d0 = d0 / (np.linalg.norm(d0) + 1e-12)
    d1 = d1 / (np.linalg.norm(d1) + 1e-12)
    c = float(np.clip(d0 @ d1, -1, 1))
    ax = np.cross(d0, d1)
    if np.linalg.norm(ax) < 1e-9:
        if c > 0:
            return np.eye(3)
        p = np.cross(d0, [1, 0, 0])
        if np.linalg.norm(p) < 1e-6:
            p = np.cross(d0, [0, 0, 1])
        return rot_axis_angle(p, np.pi)
    return rot_axis_angle(ax, np.arccos(c))


def fk_matrices(J: np.ndarray, local_rot: dict[str, np.ndarray] | None = None,
                root_translate: np.ndarray | None = None) -> np.ndarray:
    """Matrici globali (B,4,4) delle ossa; rotazioni locali nel frame del genitore."""
    B = len(NAMES)
    G = np.zeros((B, 4, 4))
    for i in range(B):
        p = PARENT[i]
        off = J[i] - (J[p] if p >= 0 else 0 * J[i])
        L = np.eye(4)
        L[:3, 3] = off if p >= 0 else J[i]
        if local_rot and NAMES[i] in local_rot:
            L[:3, :3] = local_rot[NAMES[i]]
        if root_translate is not None and p < 0:
            L[:3, 3] += root_translate
        G[i] = L if p < 0 else G[p] @ L
    return G


def ibm_from_rest(J: np.ndarray) -> np.ndarray:
    ibm = np.tile(np.eye(4), (len(NAMES), 1, 1))
    ibm[:, :3, 3] = -J
    return ibm


def skin_vertices(v: np.ndarray, jidx: np.ndarray, w: np.ndarray, skin_mats: np.ndarray) -> np.ndarray:
    """Linear blend skinning. skin_mats = G @ IBM (B,4,4)."""
    out = np.zeros_like(v)
    vh = np.concatenate([v, np.ones((len(v), 1))], 1)
    for k in range(jidx.shape[1]):
        M = skin_mats[jidx[:, k]]
        out += w[:, k:k + 1] * np.einsum("nij,nj->ni", M[:, :3, :], vh)
    return out
