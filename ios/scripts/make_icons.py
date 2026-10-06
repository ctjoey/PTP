"""Build every app icon from the Pick the Play logo (ios/scripts/PTP-Logo.png).

The logo is a rounded, silver-framed tile on a backdrop (a screenshot-style export), so it can't be
used as-is: Apple needs a 1024x1024 opaque square with NO rounded corners baked in, because iOS
applies its own corner mask. This script:

1. Finds the green field inside the frame and crops to it.
2. Measures each corner's rounding (the frame curves differ per corner) and marks the frame,
   shadow and backdrop that show there as holes.
3. Places the field on a square canvas with a small margin, so the lettering ("PICK THE PLAY",
   "LIVE PRO FOOTBALL GAME") stays clear of iOS's corner mask, and makes the margin a hole too.
4. Fills every hole with a smooth continuation of the surrounding turf ("pull-push" hole filling),
   using turf pixels only, so white lettering and chalk lines never bleed into the fill.
5. Resizes with Lanczos plus a light unsharp mask (the source is smaller than 1024), drops alpha.

Outputs:
    ios/PickThePlay/Assets.xcassets/AppIcon.appiconset/icon-1024.png  (1024, RGB, no alpha)
    static/img/apple-touch-icon.png (180), icon-512.png, icon-192.png, favicon.png (64)

Usage (needs Pillow and NumPy:  python3 -m pip install pillow numpy):
    python3 ios/scripts/make_icons.py [path/to/logo.png]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("PTP-Logo.png")
SCALE = 0.92   # artwork size inside the square; the rest is turf, keeping text inside iOS's mask
INSET = 2      # px trimmed inside the frame so its anti-aliased edge never survives


def turf(a):
    """Green-dominant pixels: the field, including its dark vignette, but not lettering or chalk."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (g > r + 12) & (g > b + 5) & (g - np.maximum(r, b) > 0.18 * g)   # not pale line/letter edges


def field_box(a):
    """The field's extent: first/last turf pixel through the centre row and column."""
    h, w = a.shape[:2]
    row, col = np.flatnonzero(turf(a[h // 2])), np.flatnonzero(turf(a[:, w // 2]))
    return row[0], col[0], row[-1] + 1, col[-1] + 1


def frame_holes(a):
    """Pixels outside each corner's rounding. The radius comes from where turf starts on the diagonal."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    holes = np.zeros((h, w), bool)
    is_turf = turf(a)
    for x0, y0, sx, sy in ((0, 0, 1, 1), (w - 1, 0, -1, 1), (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1)):
        k = next(k for k in range(min(h, w) // 2) if is_turf[y0 + sy * k, x0 + sx * k])
        radius = int(k / (1 - 2 ** -0.5)) + 6           # diagonal inset of a circle is r(1 - 1/sqrt 2)
        cx, cy = x0 + sx * radius, y0 + sy * radius
        holes |= ((xx - cx) * sx < 0) & ((yy - cy) * sy < 0) & ((xx - cx) ** 2 + (yy - cy) ** 2 > radius ** 2)
    return holes


def pull_push(img, weight):
    """Fill pixels with weight 0 from their surroundings, coarse to fine (smooth, no hard edges)."""
    if min(weight.shape) <= 2 or weight.all():
        total = weight.sum()
        mean = (img * weight[..., None]).sum((0, 1)) / max(total, 1e-6)
        return np.where(weight[..., None] > 0, img, mean)
    h, w = weight.shape
    H, W = (h + 1) // 2, (w + 1) // 2
    pw = np.zeros((H * 2, W * 2)); pw[:h, :w] = weight
    pi = np.zeros((H * 2, W * 2, 3)); pi[:h, :w] = img * weight[..., None]
    sw = pw.reshape(H, 2, W, 2).sum((1, 3))
    si = pi.reshape(H, 2, W, 2, 3).sum((1, 3))
    coarse_img = np.where(sw[..., None] > 0, si / np.maximum(sw, 1e-6)[..., None], 0)
    coarse = pull_push(coarse_img, np.minimum(sw, 1.0))
    up = np.stack([np.asarray(Image.fromarray(coarse[..., c].astype(np.float32), "F")
                              .resize((w, h), Image.BILINEAR)) for c in range(3)], -1)
    return weight[..., None] * img + (1 - weight[..., None]) * up


def build_icon(logo):
    a = np.asarray(logo, dtype=np.float64)
    l, t, r, b = field_box(a)
    field = a[t + INSET:b - INSET, l + INSET:r - INSET]
    h, w = field.shape[:2]
    holes = frame_holes(field)
    # Widen each corner hole by 3px over dark pixels only: removes the frame's inner shadow on the arc
    # without nibbling the white goal lines or lettering.
    grown = np.asarray(Image.fromarray((holes * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
    holes |= grown & (field.max(-1) < 120)

    side = int(round(max(h, w) / SCALE))
    canvas = np.zeros((side, side, 3))
    known = np.zeros((side, side), bool)
    oy, ox = (side - h) // 2, (side - w) // 2
    canvas[oy:oy + h, ox:ox + w] = field
    known[oy:oy + h, ox:ox + w] = ~holes

    filled = pull_push(canvas, (known & turf(canvas)).astype(np.float64))
    # Feather the seam a couple of pixels so texture meets the smooth fill without a hard line.
    alpha = np.asarray(Image.fromarray((known * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)),
                       dtype=np.float64)[..., None] / 255 * known[..., None]
    out = alpha * canvas + (1 - alpha) * filled
    square = Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")
    icon = square.resize((1024, 1024), Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    return icon, (l, t, r, b), (w, h), side


def main():
    logo = Image.open(SRC).convert("RGB")
    icon, box, field_size, side = build_icon(logo)
    outputs = {
        ROOT / "ios/PickThePlay/Assets.xcassets/AppIcon.appiconset/icon-1024.png": 1024,
        ROOT / "static/img/apple-touch-icon.png": 180,
        ROOT / "static/img/icon-512.png": 512,
        ROOT / "static/img/icon-192.png": 192,
        ROOT / "static/img/favicon.png": 64,
    }
    for path, size in outputs.items():
        img = icon if size == 1024 else icon.resize((size, size), Image.LANCZOS)
        img.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size})")
    print(f"source {SRC.name} {logo.size[0]}x{logo.size[1]}, field box {tuple(int(v) for v in box)}, "
          f"field {field_size[0]}x{field_size[1]} on a {side}px square")


if __name__ == "__main__":
    main()
