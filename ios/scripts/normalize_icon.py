"""Make any uploaded artwork App Store-legal: 1024x1024, opaque, full-bleed.

Drop artwork at ios/PickThePlay/Assets.xcassets/AppIcon.appiconset/artwork.png (any size). CI
writes icon-1024.png next to it. Apple rejects an icon with alpha or with rounded corners baked in,
and iOS applies its own corner mask on top, so anything left transparent or white in a corner shows
as a pale sliver on the home screen.

Two shapes of source, handled differently:

*Transparent padding around the tile* - the usual export from a design tool. Crop to the opaque
bounding box and trim nothing. The first version of this script always centre-cropped the whole
canvas, which on a 1072x976 export with an 846x838 tile inside sliced 48px off each side and cut
straight through the artwork near the edges. Crop to what is actually drawn, not to the canvas it
was drawn on.

*A flat opaque export with a rounded frame and white corners baked in* - no alpha to measure, so
fall back to a centre crop plus a small margin, which pushes the baked-in frame outside the square.

Either way, whatever alpha remains in the corners is filled with the artwork's own background
colour, so iOS's mask cuts the dark background rather than white.

    python3 normalize_icon.py IN.png OUT.png [margin] [radius]
"""
import sys
from PIL import Image, ImageDraw

src, dst = sys.argv[1], sys.argv[2]
MARGIN = float(sys.argv[3]) if len(sys.argv) > 3 else None   # None = decide from the source
RADIUS = float(sys.argv[4]) if len(sys.argv) > 4 else 0.26   # rounded-rect radius, fraction of size

im = Image.open(src).convert("RGBA")
full = (0, 0, im.size[0], im.size[1])
bbox = im.getchannel("A").getbbox() or full
padded = bbox != full
if padded:
    im = im.crop(bbox)

w, h = im.size
s = min(w, h)
im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))

margin = (0.0 if padded else 0.05) if MARGIN is None else MARGIN
if margin:
    m = int(s * margin)
    im = im.crop((m, m, s - m, s - m))
im = im.resize((1024, 1024), Image.LANCZOS)

px = im.load()
sample = px[int(1024 * 0.12), int(1024 * 0.12)]
bg = tuple(sample[:3]) if sample[3] > 200 and sum(sample[:3]) < 450 else (7, 11, 18)

mask = Image.new("L", (1024, 1024), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, 1023, 1023), radius=int(1024 * RADIUS), fill=255)
mp = mask.load()
for y in range(1024):
    for x in range(1024):
        r, g, b, a = px[x, y]
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        if a < 250 or (mp[x, y] < 128 and lum > 110):
            px[x, y] = (*bg, 255)

out = Image.new("RGB", (1024, 1024), bg)
out.paste(im, (0, 0), im)
out.save(dst, "PNG")
print(f"normalized {src} {full[2]}x{full[3]} -> {dst} 1024x1024 "
      f"({'cropped to opaque bbox ' + str(bbox) if padded else 'centre crop'}, margin {margin}, bg {bg})")
