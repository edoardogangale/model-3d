"""Contenitore mesh (posizioni, UV, facce, texture) + lettura di GLB generati dai motori."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
from PIL import Image


@dataclass
class MeshData:
    v: np.ndarray            # (N,3) float
    f: np.ndarray            # (M,3) int
    uv: np.ndarray           # (N,2) float, convenzione glTF (origine in alto a sinistra)
    tex: Image.Image | None = None
    info: dict = field(default_factory=dict)

    @property
    def tris(self) -> int:
        return int(len(self.f))

    def normals(self) -> np.ndarray:
        return vertex_normals(self.v, self.f)

    def save(self, base: Path):
        base = Path(base)
        np.savez_compressed(base.with_suffix(".npz"), v=self.v, f=self.f, uv=self.uv)
        if self.tex is not None:
            self.tex.save(base.with_suffix(".png"))

    @staticmethod
    def load(base: Path) -> "MeshData":
        base = Path(base)
        z = np.load(base.with_suffix(".npz"))
        png = base.with_suffix(".png")
        tex = Image.open(png).convert("RGBA" if _has_alpha(png) else "RGB") if png.exists() else None
        return MeshData(z["v"], z["f"], z["uv"], tex)


def _has_alpha(p: Path) -> bool:
    return Image.open(p).mode in ("RGBA", "LA")


def vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n = np.zeros_like(v)
    for k in range(3):
        np.add.at(n, f[:, k], fn)
    l = np.linalg.norm(n, axis=1, keepdims=True)
    l[l < 1e-20] = 1
    return n / l


def load_glb_mesh(path: str | Path) -> MeshData:
    """Legge un GLB/OBJ prodotto da un motore image-to-3D (anche rigged: lo scheletro del motore viene ignorato)."""
    import trimesh
    sc = trimesh.load(str(path), force="scene", process=False)
    meshes = [g for g in sc.dump(concatenate=False) if isinstance(g, trimesh.Trimesh)]
    if not meshes:
        raise ValueError("Il file del motore non contiene mesh.")
    vs, fs, uvs, texs, off = [], [], [], [], 0
    for m in meshes:
        vis = m.visual
        uv = getattr(vis, "uv", None)
        mat = getattr(vis, "material", None)
        img = None
        if mat is not None:
            img = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        if uv is None or img is None:
            raise ValueError("Mesh senza UV/texture: il motore non ha prodotto una texture. "
                             "Riprova con la texturizzazione attiva (le sole vertex color non sono supportate).")
        vs.append(np.asarray(m.vertices, float)); uvs.append(np.asarray(uv, float))
        fs.append(np.asarray(m.faces, int) + off); off += len(m.vertices); texs.append(img)
    tex = texs[0]
    if any(t is not texs[0] and t.size != texs[0].size for t in texs[1:]):
        raise ValueError("Il GLB contiene più texture diverse: non supportato (serve un atlante unico).")
    uv = np.concatenate(uvs)
    # trimesh converte le UV glTF invertendo V (origine in basso): riportiamo a origine in alto
    uv[:, 1] = 1.0 - uv[:, 1]
    return MeshData(np.concatenate(vs), np.concatenate(fs), uv, tex.convert("RGB") if tex.mode not in ("RGB", "RGBA") else tex)
