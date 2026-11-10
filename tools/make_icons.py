"""Genera los íconos PNG de la extensión (burbuja de diálogo + ondas de sonido)."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "extension" / "icons"
DESKTOP = ROOT / "desktop" / "popsay" / "assets"
BLUE = (11, 111, 216, 255)
WHITE = (255, 255, 255, 255)


def draw(size: int) -> Image.Image:
    s = size * 8  # supersampling
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.06
    # burbuja
    d.rounded_rectangle([pad, pad, s - pad, s * 0.78], radius=s * 0.22, fill=BLUE)
    d.polygon([(s * 0.24, s * 0.70), (s * 0.20, s - pad), (s * 0.46, s * 0.74)], fill=BLUE)
    # parlante
    cx, cy = s * 0.30, s * 0.42
    d.rectangle([cx - s * 0.08, cy - s * 0.07, cx, cy + s * 0.07], fill=WHITE)
    d.polygon([(cx - s * 0.01, cy - s * 0.07), (cx + s * 0.13, cy - s * 0.18), (cx + s * 0.13, cy + s * 0.18), (cx - s * 0.01, cy + s * 0.07)], fill=WHITE)
    # ondas
    w = max(2, int(s * 0.055))
    for r in (0.14, 0.25):
        box = [cx + s * 0.12 - s * r, cy - s * r, cx + s * 0.12 + s * r, cy + s * r]
        d.arc(box, start=-45, end=45, fill=WHITE, width=w)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for n in (16, 32, 48, 128):
        draw(n).save(OUT / f"icon{n}.png")
    DESKTOP.mkdir(parents=True, exist_ok=True)
    draw(256).save(DESKTOP / "icon.png")
    draw(256).save(DESKTOP / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok", sorted(p.name for p in OUT.iterdir()), sorted(p.name for p in DESKTOP.iterdir()))
