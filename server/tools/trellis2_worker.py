#!/usr/bin/env python3
"""Worker per il PC con GPU: una foto → GLB texturizzato con Microsoft TRELLIS.2 (licenza MIT).

NON COLLAUDATO in questo repository (l'ambiente di sviluppo non aveva GPU NVIDIA né accesso a Hugging Face).
Segue l'esempio ufficiale `example.py` del repository microsoft/TRELLIS.2.
Requisiti dichiarati da TRELLIS.2: Linux, GPU NVIDIA con almeno 24 GB (testato su A100/H100), CUDA Toolkit 12.4.
Uso (dentro l'ambiente conda `trellis2` creato con il loro setup.sh):
    python trellis2_worker.py --image vista_fronte.png --out modello.glb
Poi imposta, sul server dell'app:
    MODEL3D_LOCAL_CMD="ssh gpu-pc 'cd ~/TRELLIS.2 && python trellis2_worker.py --image {image} --out {out}'"
(se il PC GPU è un'altra macchina, copia prima l'immagine, p.es. con scp, oppure monta una cartella condivisa).
"""
import argparse, os
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

ap = argparse.ArgumentParser()
ap.add_argument("--image", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--decimation", type=int, default=300000, help="triangoli (l'app decima ancora a 30k dopo)")
ap.add_argument("--texture", type=int, default=2048)
a = ap.parse_args()

from PIL import Image
import torch  # noqa: F401
from trellis2.pipelines import Trellis2ImageTo3DPipeline
import o_voxel

pipeline = Trellis2ImageTo3DPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
pipeline.cuda()
image = Image.open(a.image)
mesh = pipeline.run(image)[0]
mesh.simplify(16777216)  # limite nvdiffrast, come nell'esempio ufficiale
glb = o_voxel.postprocess.to_glb(
    vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs, coords=mesh.coords, attr_layout=mesh.layout,
    voxel_size=mesh.voxel_size, aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
    decimation_target=a.decimation, texture_size=a.texture, remesh=True, remesh_band=1, remesh_project=0, verbose=True)
glb.export(a.out)   # niente WebP: l'app e il gioco leggono PNG/JPEG
print("scritto", a.out)
