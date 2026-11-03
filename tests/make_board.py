"""Crea una tavola finta (3 figure schematiche) per il test del browser. NON è una foto reale."""
import os, tempfile
from PIL import Image, ImageDraw
im = Image.new("RGB", (1200, 800), (245, 245, 248)); d = ImageDraw.Draw(im)
d.text((40, 30), "TAVOLA DI TEST - etichetta da escludere", fill=(0, 0, 0))
for x in (100, 450, 800):
    d.ellipse([x + 60, 120, x + 160, 220], fill=(226, 184, 160)); d.rectangle([x + 40, 220, x + 180, 500], fill=(240, 236, 225)); d.rectangle([x + 50, 500, x + 170, 740], fill=(52, 92, 150))
im.save(os.path.join(tempfile.gettempdir(), "board.png"))
