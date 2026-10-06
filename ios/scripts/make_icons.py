"""Build every app icon from the Pick the Play logo (ios/scripts/PTP-Logo.png).

The logo is a rounded, silver-framed tile on a backdrop (a screenshot-style export), so it can't be
used as-is: Apple needs a 1024x1024 opaque square with NO rounded corners baked in, because iOS
applies its own corner mask. This script:

1. Finds the green field inside the frame and crops to it (median edge over many rows and columns,
   so a hash mark or yard number touching the frame can't shift the crop).
2. Models the frame's inner edge as a rounded rectangle (each corner's radius measured on the
   diagonal) and removes the frame's inner shadow: turf near the frame is compared with clean turf
   a little further in, which gives the shadow's strength by position along the frame and depth;
   dividing by it relights everything under the shadow (turf, hash marks; goal and yard lines are
   relit to their own colour further in). Whatever is outside the rounding, on the frame's
   anti-aliased edge, or too dark to relight becomes a hole.
3. Picks the largest scale (up to 94%) at which every white pixel of the artwork stays >= 42px
   inside iOS's continuous-corner mask at 1024, and places the field on a square canvas; the margin
   is a hole too.
4. Fills every hole with a smooth continuation of the turf ("pull-push" hole filling), seeded only
   with clean turf (no anti-aliased rims, outlines or drop shadows of lines and letters). Each band
   between the goal lines (title, field, tagline) is filled from its own turf, so it carries on into
   the margin at its own height, and gets that turf's grain so the margin isn't an airbrushed strip.
5. Resizes with Lanczos (plus a light unsharp mask when the source is upscaled), drops alpha.

Outputs:
    ios/PickThePlay/Assets.xcassets/AppIcon.appiconset/icon-1024.png  (1024, RGB, no alpha)
    static/img/apple-touch-icon.png (180), icon-512.png, icon-192.png, favicon.png (64)

Usage (needs Pillow and NumPy:  python3 -m pip install pillow numpy):
    python3 ios/scripts/make_icons.py [path/to/logo.png]
"""
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("PTP-Logo.png")
MAX_SCALE = 0.94   # largest artwork size inside the square
CLEARANCE = 42     # px at 1024 between white lettering and iOS's corner mask (Apple's safe area ~40)
IOS_RADIUS = 0.2237  # iOS continuous-corner radius as a fraction of the icon side
INSET = 1          # px trimmed inside the frame box
EDGE = 3           # px along the frame's inner edge that are always holes (its anti-aliased rim)


def turf(a):
    """Green-dominant pixels: the field, including its dark vignette, but not lettering or chalk."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return (g > r + 12) & (g > b + 5) & (g - np.maximum(r, b) > 0.18 * g)   # not pale line/letter edges


def field_box(a):
    """The field's extent: median first/last turf pixel over the middle rows and columns."""
    h, w = a.shape[:2]
    is_turf = turf(a)

    def ends(m):   # median first and last True along each scanline (row) of m
        m = m[m.any(1)]
        if not len(m):
            raise ValueError("no green field found in the logo")
        return int(np.median(m.argmax(1))), int(np.median(m.shape[1] - m[:, ::-1].argmax(1)))
    left, right = ends(is_turf[h // 3:2 * h // 3])
    top, bottom = ends(is_turf[:, w // 3:2 * w // 3].T)
    return left, top, right, bottom


def corner_radii(field):
    """Each corner's rounding radius (TL, TR, BR, BL), from where turf starts on the diagonal."""
    h, w = field.shape[:2]
    is_turf = turf(field)
    radii = []
    for x0, y0, sx, sy in ((0, 0, 1, 1), (w - 1, 0, -1, 1), (w - 1, h - 1, -1, -1), (0, h - 1, 1, -1)):
        k = next((k for k in range(min(h, w) // 3) if is_turf[y0 + sy * k, x0 + sx * k]), 0)
        radii.append(k / (1 - 2 ** -0.5) + 4)            # diagonal inset of a circle is r(1 - 1/sqrt 2)
    return radii


def frame_geometry(h, w, radii):
    """Distance (px) of every pixel from the frame's inner edge (negative outside the rounding), the
    inward normal there, and the position along the frame's perimeter."""
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    sides = np.stack([yy, w - xx, h - yy, xx])                     # top, right, bottom, left
    nearest = sides.argmin(0)
    d = sides.min(0)
    ny = np.choose(nearest, [1.0, 0.0, -1.0, 0.0])
    nx = np.choose(nearest, [0.0, -1.0, 0.0, 1.0])
    s = np.choose(nearest, [xx, w + yy, w + h + (w - xx), 2 * w + h + (h - yy)])
    tl, tr, br, bl = radii
    for r, cx, cy, sx, sy, pos in (
            (tl, tl, tl, 1, 1, lambda qx, qy: qx - qy),
            (tr, w - tr, tr, -1, 1, lambda qx, qy: w + qy - (w - qx)),
            (br, w - br, h - br, -1, -1, lambda qx, qy: w + h + (w - qx) - (h - qy)),
            (bl, bl, h - bl, 1, -1, lambda qx, qy: 2 * w + h + (h - qy) - qx)):
        zone = ((xx - cx) * sx < 0) & ((yy - cy) * sy < 0)
        dx, dy = xx[zone] - cx, yy[zone] - cy
        rho = np.maximum(np.hypot(dx, dy), 1e-6)
        d[zone] = r - rho
        nx[zone], ny[zone] = -dx / rho, -dy / rho
        s[zone] = pos(cx + dx / rho * r, cy + dy / rho * r)
    return d, ny, nx, s % (2 * (w + h))


def along_normal(img, mask, ys, xs, d, ny, nx, depths):
    """Pixels of img (NaN where mask is False) at the given depths along each pixel's inward normal."""
    h, w = mask.shape
    out = []
    for t in depths:
        step = t - d[ys, xs]
        sy = np.clip(np.round(ys + ny[ys, xs] * step).astype(int), 0, h - 1)
        sx = np.clip(np.round(xs + nx[ys, xs] * step).astype(int), 0, w - 1)
        out.append(np.where(mask[sy, sx][:, None], img[sy, sx], np.nan))
    return np.stack(out)


def remove_shadow(field, radii):
    """Relight the frame's inner shadow; return the relit field and the pixels to treat as holes."""
    h, w = field.shape[:2]
    d, ny, nx, s = frame_geometry(h, w, radii)
    d1 = max(8, round(0.04 * min(h, w)))      # the shadow has faded out by here...
    d2 = round(1.5 * d1)                      # ...so what lies between d1 and d2 is the clean reference
    ys, xs = np.nonzero((d >= 0) & (d < d1))
    obs = field[ys, xs]
    depths = np.linspace(d1, d2, 7)

    # Turf: reference = median of clean turf further in along the normal; the shadow profile is the
    # ratio observed/reference, binned by position along the frame and depth, smoothed along the frame.
    clean = clean_turf(field, d >= EDGE)
    samples = along_normal(field, clean, ys, xs, d, ny, nx, depths)
    use = np.isfinite(samples[..., 0]).any(0) & clean[ys, xs] & (d[ys, xs] >= EDGE)
    ref = np.nanmedian(samples[:, use], 0)
    nbins = max(8, int(2 * (w + h) / 24))
    b = (s[ys, xs] / (2 * (w + h)) * nbins).astype(int) % nbins
    di = np.minimum(d[ys, xs].astype(int), d1 - 1)
    key = b * d1 + di
    # One gain for all channels, from their sum: R and B are near 0 in dark turf, so their own
    # ratios would be noise (and would tint white lines when divided out).
    num = np.bincount(key[use], obs[use].sum(-1), nbins * d1).reshape(nbins, d1)
    den = np.bincount(key[use], ref.sum(-1), nbins * d1).reshape(nbins, d1)

    def smooth(v):   # along the frame
        return sum(k * np.roll(v, i - 2, 0) for i, k in enumerate((1, 2, 3, 2, 1)))
    gain = np.where(smooth(den) > 0, smooth(num) / np.maximum(smooth(den), 1e-6), np.nan)
    gain[:, -1] = np.where(np.isnan(gain[:, -1]), 1.0, gain[:, -1])
    for k in range(d1 - 2, -1, -1):                       # no data (e.g. the frame edge): use deeper
        gain[:, k] = np.where(np.isnan(gain[:, k]), gain[:, k + 1], gain[:, k])
    # A shadow only darkens, and fades with depth: keep the profile rising and at most 1, so noise
    # in the reference (lettering's own drop shadow, vignette) can't grey out white lettering.
    rising = (np.maximum.accumulate(gain, 1) + np.minimum.accumulate(gain[:, ::-1], 1)[:, ::-1]) / 2
    shade = np.clip(rising, 0.2, 1.0)[b, di]
    boost = 1 / shade

    # Chalk lines running into the side frame (goal and yard lines) darken more than turf under the
    # shadow; relight each such pixel to the line's own colour further in along the same row.
    bright = field.min(-1) > 170
    chalk = along_normal(field, bright, ys, xs, d, ny, nx, depths)
    line_end = ((np.isfinite(chalk[..., 0]).sum(0) >= 4) & (obs.min(-1) > 90) & (np.abs(nx[ys, xs]) > 0.95))
    chalk_ref = np.nanmedian(chalk[:, line_end], 0).sum(-1)
    boost[line_end] = np.clip(chalk_ref / obs[line_end].sum(-1), boost[line_end], 2.0)

    relit = field.copy()
    relit[ys, xs] = np.clip(obs * boost[:, None], 0, 255)
    holes = d < EDGE                                      # outside the rounding or on its edge
    holes[ys, xs] |= shade < 0.55                         # too dark to relight reliably
    return relit, holes


def squircle(n):
    """iOS's continuous-corner mask at n x n (True inside), 4x supersampled like the real thing."""
    segs = (((1.52866483, 0), (1.08849323, 0), (0.86840689, 0), (0.66993427, 0.06549600)),
            ((0.66993427, 0.06549600), (0.37282392, 0.16299700), (0.16299700, 0.37282392), (0.06549600, 0.66993427)),
            ((0.06549600, 0.66993427), (0, 0.86840689), (0, 1.08849323), (0, 1.52866483)))
    t = np.linspace(0, 1, 40)[:, None]
    local = np.concatenate([(1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1
                            for p0, c1, c2, p1 in (map(np.array, seg) for seg in segs)]) * IOS_RADIUS * n
    x, y = local[:, 0], local[:, 1]
    pts = np.concatenate([np.stack(p, 1) for p in ((n - x, y), (n - y, n - x), (x, n - y), (y, x))])
    ss = 4
    m = Image.new("L", (n * ss, n * ss), 0)
    ImageDraw.Draw(m).polygon([tuple(p) for p in pts * ss], fill=255)
    return np.asarray(m.resize((n, n), Image.LANCZOS)) > 127


def distance_to(mask, cap):
    """Euclidean distance from every pixel to the nearest True pixel of mask, capped at cap."""
    h, w = mask.shape
    g = np.where(mask, 0.0, cap + 1.0)
    for y in range(1, h):                      # vertical distance within each column
        g[y] = np.minimum(g[y], g[y - 1] + 1)
    for y in range(h - 2, -1, -1):
        g[y] = np.minimum(g[y], g[y + 1] + 1)
    best = g ** 2
    for dx in range(1, cap + 1):               # then the nearest column within reach
        best[:, dx:] = np.minimum(best[:, dx:], g[:, :-dx] ** 2 + dx * dx)
        best[:, :-dx] = np.minimum(best[:, :-dx], g[:, dx:] ** 2 + dx * dx)
    return np.minimum(np.sqrt(best), cap)


@lru_cache(maxsize=1)
def mask_clearance():
    """Distance (px at 1024) from every pixel to the part of the icon iOS's mask cuts off."""
    return distance_to(~squircle(1024), CLEARANCE + 8)


def fit_scale(field, holes):
    """Largest artwork scale <= MAX_SCALE at which all white pixels clear the iOS mask."""
    h, w = field.shape[:2]
    ys, xs = np.nonzero((field.min(-1) > 190) & ~holes)
    clear = mask_clearance()
    for scale in np.arange(MAX_SCALE, 0.5, -0.002):
        side = int(round(max(h, w) / scale))
        oy, ox = (side - h) // 2, (side - w) // 2
        iy = np.clip(((oy + ys + 0.5) * 1024 / side).astype(int), 0, 1023)
        ix = np.clip(((ox + xs + 0.5) * 1024 / side).astype(int), 0, 1023)
        if not len(ys) or clear[iy, ix].min() >= CLEARANCE:
            return scale
    return 0.5


def upsample(a, h, w):
    return np.stack([np.asarray(Image.fromarray(a[..., c].astype(np.float32), "F").resize((w, h), Image.BILINEAR))
                     for c in range(a.shape[-1])], -1)


def pull_push(img, weight):
    """Fill pixels with weight 0 from their surroundings, coarse to fine (smooth, no hard edges).
    Coarse cells are interpolated weighted by how much real data they hold, so an empty cell (the
    canvas margin, the padding past its edge) doesn't drag in far-away colours."""
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
    coarse_w = np.minimum(sw, 1.0)
    coarse = pull_push(np.where(sw[..., None] > 0, si / np.maximum(sw, 1e-6)[..., None], 0), coarse_w)
    # Pad by one cell so interpolation at the far edge sees the last real cell, not a black border.
    stack = np.concatenate([coarse * coarse_w[..., None], coarse_w[..., None], coarse], -1)
    stack = np.pad(stack, ((0, 1), (0, 1), (0, 0)), mode="edge")
    up = upsample(stack, 2 * H + 2, 2 * W + 2)[:h, :w]
    soft = 0.05   # fall back smoothly to the plain fill where no nearby coarse cell has data
    up = (up[..., :3] + soft * up[..., 4:]) / (up[..., 3:4] + soft)
    return weight[..., None] * img + (1 - weight[..., None]) * up


def window_sum(a, r):
    """Sum of a over the (2r+1) x (2r+1) window around every pixel (zero outside the image)."""
    for axis in (0, 1):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(a.astype(np.float64), pad), axis)
        n = a.shape[axis]
        a = np.take(c, np.arange(2 * r + 1, n + 2 * r + 1), axis) - np.take(c, np.arange(n), axis)
    return a


def dilate(mask, px):
    return window_sum(mask, px) > 0.5


def clean_turf(img, inside):
    """Turf that shows the field's own colour: not the anti-aliased rims of lettering, lines and
    marks, nor their dark outlines and drop shadows (judged against the turf around them)."""
    is_turf = turf(img) & ~dilate(~turf(img) & inside, 2)
    lum = img.sum(-1)
    local = window_sum(lum * is_turf, 8) / np.maximum(window_sum(is_turf, 8), 1)
    return is_turf & (lum > 0.8 * local) & (lum < 1.25 * local)


def band_cuts(field, holes):
    """Full-width lines (rows with almost no turf) where the turf colour changes across them: the
    goal lines between the title band, the field and the tagline band. Returns (first, end) rows."""
    h, w = holes.shape
    is_turf = clean_turf(field, ~holes) & ~holes
    across = (~is_turf)[:, w // 20:w - w // 20].mean(1) > 0.9
    edges = np.flatnonzero(np.diff(np.r_[0, across.astype(int), 0]))
    runs = [(a, b) for a, b in zip(edges[::2], edges[1::2]) if a > 0.02 * h and b < 0.98 * h]
    lum = field.sum(-1)

    def level(y0, y1):
        v = lum[y0:y1][is_turf[y0:y1]]
        return np.median(v) if len(v) > 100 else None
    cuts = []
    for i, (a, b) in enumerate(runs):
        above = level(cuts[-1][1] if cuts else 0, a)
        below = level(b, runs[i + 1][0] if i + 1 < len(runs) else h)
        if above is not None and below is not None and abs(above - below) > 0.35 * max(above, below):
            cuts.append((a, b))
    return cuts


def turf_grain(canvas, seeds, size=256, win=32):
    """A seamless size x size pattern of relative turf grain with the field's own spectrum and
    strength: averaged over clean win x win windows of turf, then given random (seeded) phases."""
    lum = canvas.sum(-1) + 1
    low = window_sum(lum * seeds, 8) / np.maximum(window_sum(seeds, 8), 1e-6)
    rel = lum / np.maximum(low, 1) - 1
    power, n = 0, 0
    for y in range(0, len(seeds) - win, win // 2):
        for x in range(0, len(seeds) - win, win // 2):
            if seeds[y:y + win, x:x + win].all():
                patch = rel[y:y + win, x:x + win]
                power = power + np.abs(np.fft.fft2(patch - patch.mean())) ** 2
                n += 1
    if n < 4:
        return np.zeros((size, size))
    psd = Image.fromarray(np.fft.fftshift(power / n).astype(np.float32), "F").resize((size, size), Image.BILINEAR)
    amp = np.sqrt(np.maximum(np.fft.ifftshift(np.asarray(psd)), 0))
    noise = np.fft.fft2(np.random.default_rng(0).standard_normal((size, size)))
    tex = np.real(np.fft.ifft2(noise * amp))
    clean = seeds & (window_sum(~seeds, 2) < 0.5)
    return (tex - tex.mean()) / max(tex.std(), 1e-9) * rel[clean].std()


def band_fill(canvas, seeds, cuts):
    """Fill every hole from the turf of its own band, so the title band, the field and the tagline
    band each carry on into the margin at their own height (blending across the goal lines), with
    that band's own turf grain so the fill doesn't read as a smooth, airbrushed strip."""
    side = len(canvas)
    rows = np.arange(side) + 0.5
    below = [np.clip((rows - a) / max(b - a, 1), 0, 1) for a, b in cuts]   # 0 above a cut, 1 below
    weights = [1 - below[0]] + [below[k] - below[k + 1] for k in range(len(cuts) - 1)] + [below[-1]] \
        if cuts else [np.ones(side)]
    filled = 0
    for wt in weights:
        own = seeds * (wt > 0.999)[:, None]                               # seeds wholly in this band
        own = own if own.any() else seeds
        tile = turf_grain(canvas, own > 0)
        grain = np.tile(tile, (side // len(tile) + 1, side // len(tile) + 1))[:side, :side]
        fill = pull_push(canvas, own) * (1 + np.clip(grain, -0.15, 0.15))[..., None]
        filled = filled + wt[:, None, None] * fill
    return filled


def build_icon(logo):
    a = np.asarray(logo.convert("RGB"), dtype=np.float64)
    l, t, r, b = field_box(a)
    field = a[t + INSET:b - INSET, l + INSET:r - INSET]
    h, w = field.shape[:2]
    field, holes = remove_shadow(field, corner_radii(field))

    scale = fit_scale(field, holes)
    side = int(round(max(h, w) / scale))
    canvas = np.zeros((side, side, 3))
    known = np.zeros((side, side), bool)
    oy, ox = (side - h) // 2, (side - w) // 2
    canvas[oy:oy + h, ox:ox + w] = field
    known[oy:oy + h, ox:ox + w] = ~holes

    # Seed the fill with clean turf only, so no line, letter or drop shadow is smeared into it.
    seeds = (known & clean_turf(canvas, known)).astype(np.float64)
    filled = band_fill(canvas, seeds, [(y0 + oy, y1 + oy) for y0, y1 in band_cuts(field, holes)])
    # Feather the seam a couple of pixels so texture meets the smooth fill without a hard line.
    alpha = np.asarray(Image.fromarray((known * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)),
                       dtype=np.float64)[..., None] / 255 * known[..., None]
    out = alpha * canvas + (1 - alpha) * filled
    square = Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")
    icon = square.resize((1024, 1024), Image.LANCZOS)
    if side < 900:   # noticeably upscaled: restore a little crispness
        icon = icon.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
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
