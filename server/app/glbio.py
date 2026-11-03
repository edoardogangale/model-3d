"""Scrittura e lettura GLB indipendenti (il validatore rilegge i byte del file, non la scena in memoria)."""
from __future__ import annotations
import json, struct, io
from pathlib import Path
import numpy as np
from PIL import Image
from . import skeleton as sk

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class _Buf:
    def __init__(self):
        self.data = bytearray(); self.views = []; self.acc = []

    def view(self, raw: bytes, target=None) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        d = {"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw)}
        if target: d["target"] = target
        self.data += raw; self.views.append(d); return len(self.views) - 1

    def accessor(self, arr: np.ndarray, ctype: int, typ: str, target=None, minmax=False) -> int:
        a = np.ascontiguousarray(arr.astype(CT[ctype]))
        bv = self.view(a.tobytes(), target)
        d = {"bufferView": bv, "componentType": ctype, "count": int(len(a)), "type": typ}
        if minmax:
            d["min"] = [float(x) for x in a.min(0)]; d["max"] = [float(x) for x in a.max(0)]
        self.acc.append(d); return len(self.acc) - 1


def encode_texture(tex: Image.Image, max_size: int = 2048, jpeg_quality: int = 90) -> tuple[bytes, str, tuple[int, int]]:
    t = tex
    if max(t.size) > max_size:
        s = max_size / max(t.size)
        t = t.resize((max(1, round(t.size[0] * s)), max(1, round(t.size[1] * s))), Image.LANCZOS)
    bio = io.BytesIO()
    if t.mode == "RGBA" and np.asarray(t)[..., 3].min() < 255:
        t.save(bio, "PNG", optimize=True); mime = "image/png"
    else:
        t.convert("RGB").save(bio, "JPEG", quality=jpeg_quality, optimize=True); mime = "image/jpeg"
    return bio.getvalue(), mime, t.size


def write_skinned_glb(path: str | Path, v, n, uv, f, J, jidx, w, tex: Image.Image, *,
                      prefix: str = "", max_tex: int = 2048, name: str = "Character", extras: dict | None = None) -> dict:
    b = _Buf()
    f = np.asarray(f)
    idx_type = 5125
    a_pos = b.accessor(v, 5126, "VEC3", 34962, True)
    a_nor = b.accessor(n, 5126, "VEC3", 34962)
    a_uv = b.accessor(uv, 5126, "VEC2", 34962)
    a_jnt = b.accessor(jidx, 5123, "VEC4", 34962)
    a_wgt = b.accessor(w, 5126, "VEC4", 34962)
    a_idx = b.accessor(f.reshape(-1), idx_type, "SCALAR", 34963)
    ibm = sk.ibm_from_rest(J)
    a_ibm = b.accessor(ibm.transpose(0, 2, 1).reshape(len(sk.NAMES), 16), 5126, "MAT4")  # column-major
    img_bytes, mime, tsize = encode_texture(tex, max_tex)
    iv = b.view(img_bytes)

    nodes = [{"name": name},
             {"name": "Body", "mesh": 0, "skin": 0}]
    first_bone = 2
    for i, nm in enumerate(sk.NAMES):
        p = sk.PARENT[i]
        t = (J[i] - (J[p] if p >= 0 else 0)).astype(float)
        nd = {"name": sk.bone_name(nm, prefix), "translation": [float(x) for x in t]}
        ch = [first_bone + j for j in range(len(sk.NAMES)) if sk.PARENT[j] == i]
        if ch: nd["children"] = ch
        nodes.append(nd)
    # il nodo 2 è Hips (radice scheletro)
    gltf = {
        "asset": {"version": "2.0", "generator": "model-3d pipeline"},
        "scene": 0, "scenes": [{"nodes": [1, 2]}], "nodes": nodes,
        "skins": [{"name": "Armature", "joints": [first_bone + i for i in range(len(sk.NAMES))],
                   "skeleton": first_bone, "inverseBindMatrices": a_ibm}],
        "meshes": [{"name": "Body", "primitives": [{"attributes": {
            "POSITION": a_pos, "NORMAL": a_nor, "TEXCOORD_0": a_uv, "JOINTS_0": a_jnt, "WEIGHTS_0": a_wgt},
            "indices": a_idx, "material": 0}]}],
        "materials": [{"name": "BodyMat", "pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0}, "metallicFactor": 0.0, "roughnessFactor": 0.9}}],
        "textures": [{"source": 0, "sampler": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": iv, "mimeType": mime, "name": "BaseColor"}],
        "accessors": b.acc, "bufferViews": b.views, "buffers": [{"byteLength": len(b.data)}],
    }
    if extras:
        gltf["asset"]["extras"] = extras
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_ = bytes(b.data) + b"\0" * ((4 - len(b.data) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_)
    out = struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js + \
        struct.pack("<I4s", len(bin_), b"BIN\0") + bin_
    Path(path).write_bytes(out)
    return {"bytes": len(out), "texture_size": list(tsize), "mime": mime}


class GLB:
    def __init__(self, path: str | Path):
        raw = Path(path).read_bytes()
        self.size = len(raw)
        if raw[:4] != b"glTF":
            raise ValueError("Non è un GLB (magic errato)")
        ver, total = struct.unpack("<II", raw[4:12])
        if total != len(raw):
            raise ValueError(f"Lunghezza dichiarata {total} ≠ dimensione file {len(raw)}")
        pos, self.json, self.bin = 12, None, b""
        while pos < len(raw):
            ln, ty = struct.unpack("<I4s", raw[pos:pos + 8]); pos += 8
            chunk = raw[pos:pos + ln]; pos += ln
            if ty == b"JSON": self.json = json.loads(chunk.decode())
            elif ty == b"BIN\0": self.bin = chunk
        if self.json is None:
            raise ValueError("Chunk JSON mancante")

    def accessor(self, i: int) -> np.ndarray:
        a = self.json["accessors"][i]; bv = self.json["bufferViews"][a["bufferView"]]
        dt = np.dtype(CT[a["componentType"]]); nc = NC[a["type"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride") or dt.itemsize * nc
        if stride == dt.itemsize * nc:
            arr = np.frombuffer(self.bin, dt, a["count"] * nc, off).reshape(a["count"], nc)
        else:
            arr = np.stack([np.frombuffer(self.bin, dt, nc, off + k * stride) for k in range(a["count"])])
        return arr.astype(np.float64) if dt.kind == "f" else arr.copy()

    def image(self, i: int) -> Image.Image:
        im = self.json["images"][i]
        if "uri" in im: raise ValueError("Immagine esterna")
        bv = self.json["bufferViews"][im["bufferView"]]
        o = bv.get("byteOffset", 0)
        return Image.open(io.BytesIO(self.bin[o:o + bv["byteLength"]]))


def write_static_glb(path, v, n, uv, f, tex: Image.Image, max_tex: int = 1024):
    """GLB statico solo per anteprima (mai presentato come avatar)."""
    b = _Buf()
    a_pos = b.accessor(v, 5126, "VEC3", 34962, True); a_nor = b.accessor(n, 5126, "VEC3", 34962)
    a_uv = b.accessor(uv, 5126, "VEC2", 34962); a_idx = b.accessor(np.asarray(f).reshape(-1), 5125, "SCALAR", 34963)
    img, mime, _ = encode_texture(tex, max_tex); iv = b.view(img)
    g = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "Preview"}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": a_pos, "NORMAL": a_nor, "TEXCOORD_0": a_uv}, "indices": a_idx, "material": 0}]}],
         "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0, "roughnessFactor": 0.9}}],
         "textures": [{"source": 0}], "images": [{"bufferView": iv, "mimeType": mime}],
         "accessors": b.acc, "bufferViews": b.views, "buffers": [{"byteLength": len(b.data)}]}
    js = json.dumps(g, separators=(",", ":")).encode(); js += b" " * ((4 - len(js) % 4) % 4)
    bin_ = bytes(b.data) + b"\0" * ((4 - len(b.data) % 4) % 4)
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(bin_)) + struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(bin_), b"BIN\0") + bin_)
