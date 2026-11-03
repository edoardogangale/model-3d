"""Personaggio SINTETICO di prova (NON è una ricostruzione da foto).

Serve solo a collaudare pulizia, rigging, esportazione e validazione senza GPU né crediti.
Qualsiasi GLB derivato da questo motore è marcato `reconstruction=False` e non può essere scaricato come "avatar pronto".
"""
from __future__ import annotations
import numpy as np
from PIL import Image, ImageDraw
from ..meshdata import MeshData


def _cap(p, a, b, ra, rb):
    ab = b - a; t = np.clip(((p - a) @ ab) / (ab @ ab), 0, 1)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1) - (ra + t * (rb - ra))


def truth_joints(arm_deg=12.0) -> dict[str, np.ndarray]:
    s = np.sin(np.deg2rad(arm_deg)); c = np.cos(np.deg2rad(arm_deg))
    J = {"Hips": [0, .90, 0], "Spine": [0, .99, 0], "Spine1": [0, 1.12, 0], "Spine2": [0, 1.25, 0],
         "Neck": [0, 1.40, 0], "Head": [0, 1.49, 0.01], "HeadTop_End": [0, 1.70, 0.01]}
    for n, sg in (("Left", 1), ("Right", -1)):
        sh = np.array([sg * .19, 1.40, 0]); d = np.array([sg * s, -c, 0])
        J[n + "Shoulder"] = [sg * .06, 1.40, 0]; J[n + "Arm"] = sh
        J[n + "ForeArm"] = sh + d * .29; J[n + "Hand"] = sh + d * .54
        J[n + "UpLeg"] = [sg * .10, .90, 0]; J[n + "Leg"] = [sg * .11, .49, .01]; J[n + "Foot"] = [sg * .115, .08, 0]
        J[n + "ToeBase"] = [sg * .115, .04, .12]
    return {k: np.array(v, float) for k, v in J.items()}


def build(arm_deg=12.0, res=0.012, rotate_y_deg=0.0, floor=True, fragment=True, offset=(0.3, 0.0, -0.2)) -> MeshData:
    import trimesh
    from skimage import measure
    P = lambda *a: np.array(a, float)
    caps = [(P(0, .88, 0), P(0, 1.0, 0), .15, .15), (P(0, 1.0, 0), P(0, 1.36, 0), .15, .17),
            (P(0, 1.40, 0), P(0, 1.52, 0), .05, .05), (P(0, 1.60, .01), P(0, 1.60, .01), .105, .105),
            (P(0, 1.62, -.075), P(0, 1.28, -.10), .06, .07)]  # capelli lunghi sulla schiena
    for sg in (1, -1):
        s = np.sin(np.deg2rad(arm_deg)); c = np.cos(np.deg2rad(arm_deg))
        sh = P(sg * .19, 1.40, 0); d = P(sg * s, -c, 0)
        caps += [(sh, sh + d * .29, .045, .036), (sh + d * .29, sh + d * .54, .036, .03), (sh + d * .54, sh + d * .63, .03, .025)]
        caps += [(P(sg * .10, .90, 0), P(sg * .11, .49, .01), .085, .06), (P(sg * .11, .49, .01), P(sg * .115, .08, 0), .06, .04),
                 (P(sg * .115, .06, 0), P(sg * .115, .04, .17), .04, .035)]
    lo = P(-.5, -.05, -.4); hi = P(.5, 1.75, .45)
    g = [np.arange(lo[i], hi[i], res) for i in range(3)]
    X, Y, Z = np.meshgrid(*g, indexing="ij"); pts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], 1)
    sdf = np.full(len(pts), 9.0)
    for a, b, ra, rb in caps:
        sdf = np.minimum(sdf, _cap(pts, a, b, ra, rb) if np.linalg.norm(b - a) > 1e-9 else np.linalg.norm(pts - a, axis=1) - ra)
    vol = sdf.reshape(X.shape)
    v, f, _, _ = measure.marching_cubes(vol, 0.0, spacing=(res, res, res))
    v = v + lo
    H = v[:, 1].max()
    # UV: proiezione frontale; texture procedurale con zone (pelle, camicia, jeans, scarpe, capelli)
    uv = np.stack([v[:, 0] / 1.0 + 0.5, 1.0 - (v[:, 1] + 0.05) / (H + 0.1)], 1)
    tex = Image.new("RGB", (512, 1024), (226, 184, 160)); dr = ImageDraw.Draw(tex)
    Hh = H + 0.1
    def yb(y): return int((1 - (y + 0.05) / Hh) * 1024)
    dr.rectangle([0, yb(1.70), 512, yb(1.50)], fill=(226, 184, 160))          # volto
    dr.rectangle([0, yb(1.50), 512, yb(.95)], fill=(240, 236, 225))           # camicetta
    rng = np.random.default_rng(1)
    for _ in range(260):
        x, y = rng.integers(0, 512), rng.integers(yb(1.48), yb(.97))
        dr.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(214, 110, 120))
    dr.rectangle([0, yb(.95), 512, yb(.09)], fill=(52, 92, 150))              # jeans
    dr.rectangle([0, yb(.09), 512, 1024], fill=(250, 250, 250))               # scarpe
    parts = [MeshData(v, f.copy(), uv, tex)]
    out_v, out_f, out_uv, off = [v], [f], [uv], len(v)
    if floor:
        c = trimesh.creation.cylinder(radius=0.7, height=0.01, sections=48).apply_translation([0, -0.06, 0]).apply_transform(
            trimesh.transformations.rotation_matrix(0, [0, 1, 0]))
        c.vertices[:, 1] += 0.0
        out_v.append(np.asarray(c.vertices)); out_f.append(np.asarray(c.faces) + off); out_uv.append(np.zeros((len(c.vertices), 2)) + .5); off += len(c.vertices)
    if fragment:
        s = trimesh.creation.icosphere(subdivisions=2, radius=0.03).apply_translation([0.45, 1.2, 0.3])
        out_v.append(np.asarray(s.vertices)); out_f.append(np.asarray(s.faces) + off); out_uv.append(np.zeros((len(s.vertices), 2)) + .5)
    V = np.concatenate(out_v); F = np.concatenate(out_f); UV = np.concatenate(out_uv)
    a = np.deg2rad(rotate_y_deg); R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    V = V @ R.T * 1.0 + np.array(offset)
    return MeshData(V, F, UV, tex, {"fixture": True})


# ---------------------------------------------------------------- adattatore del motore
from pathlib import Path
from .base import Engine


class FixtureEngine(Engine):
    id = "fixture"
    label = "Manichino di prova (NON è una ricostruzione)"
    reconstruction = False
    needs = "Nessuno. Genera un personaggio sintetico per collaudare pulizia, rig, esportazione e validazione."

    def run(self, jd: Path, views, options, log, cancelled) -> dict:
        import trimesh
        from trimesh.visual import TextureVisuals
        from trimesh.visual.material import PBRMaterial
        log("Genero il manichino sintetico (le foto NON vengono usate)")
        m = build(rotate_y_deg=float(options.get("fixture_rotate_y", 0)))
        uv = m.uv.copy(); uv[:, 1] = 1 - uv[:, 1]
        tm = trimesh.Trimesh(m.v, m.f, visual=TextureVisuals(uv=uv, material=PBRMaterial(baseColorTexture=m.tex)), process=False)
        (jd / "raw").mkdir(exist_ok=True)
        tm.export(jd / "raw" / "model.glb")
        return {"usage": {"used": [], "unused": [v["id"] for v in views], "notes": ["Motore di prova: nessuna foto è stata usata."]}}
