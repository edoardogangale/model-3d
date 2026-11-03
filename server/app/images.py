"""Gestione immagini: ritagli delle viste, esclusione di scritte/etichette, rimozione dello sfondo."""
from __future__ import annotations
import numpy as np
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps
from scipy import ndimage

ROLES = {
    "front": "Fronte", "back": "Retro", "profile_left": "Profilo sinistro (anatomico)", "profile_right": "Profilo destro (anatomico)",
    "three_quarter": "Tre quarti", "face": "Viso (dettaglio)", "hair": "Capelli (dettaglio)", "clothing": "Vestiti (dettaglio)",
    "accessory": "Accessorio (dettaglio)",
}
BODY_ROLES = ("front", "back", "profile_left", "profile_right", "three_quarter")


def open_image(p: Path) -> Image.Image:
    im = Image.open(p)
    im = ImageOps.exif_transpose(im)
    im.load()
    return im


def remove_background(im: Image.Image, tol: float = 28.0, feather: float = 1.0) -> Image.Image:
    """Rimozione semplice dello sfondo: colore stimato dal bordo, regioni connesse al bordo. Funziona su sfondi uniformi
    (come le tavole di riferimento); NON è segmentazione semantica. Controlla sempre l'anteprima."""
    rgb = np.asarray(im.convert("RGB")).astype(np.float32)
    h, w, _ = rgb.shape
    b = max(2, int(min(h, w) * 0.015))
    border = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3), rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(rgb - bg, axis=2)
    cand = dist < tol
    lab, n = ndimage.label(cand)
    edge_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bgmask = np.isin(lab, list(edge_labels))
    fg = ~bgmask
    fg = ndimage.binary_opening(fg, iterations=1)
    # tieni il componente principale (il soggetto) e riempi i buchi interni
    l2, n2 = ndimage.label(fg)
    if n2:
        sizes = ndimage.sum(fg, l2, range(1, n2 + 1))
        fg = np.isin(l2, [i + 1 for i, s in enumerate(sizes) if s >= 0.05 * sizes.max()])
    fg = ndimage.binary_fill_holes(fg)
    a = Image.fromarray((fg * 255).astype(np.uint8))
    if feather > 0: a = a.filter(ImageFilter.GaussianBlur(feather))
    out = im.convert("RGBA"); out.putalpha(a)
    return out


def make_view(src: Path, crop: list[float], exclude: list[list[float]], remove_bg: bool, tol: float, dst_png: Path, dst_thumb: Path) -> dict:
    """crop/exclude in pixel dell'immagine ORIGINALE: [x, y, w, h]."""
    im = open_image(src).convert("RGB")
    x, y, w, h = [int(round(v)) for v in crop]
    x = max(0, min(x, im.width - 1)); y = max(0, min(y, im.height - 1))
    w = max(8, min(w, im.width - x)); h = max(8, min(h, im.height - y))
    c = im.crop((x, y, x + w, y + h))
    arr = np.asarray(c).copy()
    bgcol = np.median(np.concatenate([arr[:3].reshape(-1, 3), arr[-3:].reshape(-1, 3), arr[:, :3].reshape(-1, 3), arr[:, -3:].reshape(-1, 3)]), axis=0).astype(np.uint8)
    for ex in exclude or []:   # scritte, etichette, cornici: riempite col colore di sfondo prima della rimozione
        ex0, ey0 = int(ex[0] - x), int(ex[1] - y)
        arr[max(0, ey0):max(0, ey0 + int(ex[3])), max(0, ex0):max(0, ex0 + int(ex[2]))] = bgcol
    c = Image.fromarray(arr)
    out = remove_background(c, tol) if remove_bg else c.convert("RGBA")
    out.save(dst_png)
    t = out.copy(); t.thumbnail((360, 360)); t.save(dst_thumb)
    cov = float(np.asarray(out.split()[-1]).astype(float).mean() / 255)
    return {"size": [out.width, out.height], "foreground_ratio": cov}
