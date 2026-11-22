"""Cutout: bake images of a textured mesh from a chosen set of angles into one atlas, plus a small JSON "kit".

The tool only bakes. It does not decide how the images are shown at run time: the kit lists every view (angles,
axes, atlas rectangle) and the world size, and the engine or level editor picks a display mode per placement
(camera-facing, nearest baked view, axis-locked, fixed) and a shape (single, cross, star). See README.md.

Input : one OBJ (positions, UVs with V upwards, faces grouped by `usemtl`) and one image per material.
Output: <out>.png       the atlas, a grid of equal cells, one per view
        <out>.kit.json  the kit the engine reads: id, atlas, cell size, views, half size, anchor, alpha
        <out>.report.json  costs: texture bytes, atlas fill, opaque share, texel size, colour gain
        <out>.obj       optional preview shape: one upright quad per level view, crossed at the pivot

usage: python -B src/cutout.py <config.json>      (config keys: see README.md)
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import card_views as cv                                # noqa: E402

RENDER_SCALE = 4           # each cell is rendered this many times larger, then box-shrunk (smooth cut edges)
GREY = 128.0               # 0..255: colour of a material that has no texture
BLEED_STEPS = 8            # texels the edge colour is spread into the transparent area
VQ_CODEBOOK_BYTES = 2048   # bytes: codebook of a vector-quantised texture (2 bits per texel + this)
GAIN_RANGE = (0.8, 1.25)   # limits of the colour-match gain per channel (a larger step means bad input, not a tint)
MAX_ATLAS_PX = 2048        # px: largest atlas side the layout search tries
POLE_DEGREES = 89.0        # a view steeper than this looks straight down / up and needs its own image axes
DEFAULTS = {"angles": 2, "span": None, "elevations": [0.0], "rings": None, "cell": [64, 64], "square_atlas": True,
            "alpha": "test", "id": None, "up": "Z", "handedness": "right", "gutter": 0.02, "match_colour": True, "preview_obj": True,
            "vertex_bytes": 24, "index_bytes": 2, "vq_texture": True, "textures": {}}


def read_obj(path):
    """OBJ file -> (positions (V,3), uvs (V,2) with V upwards, triangles (T,3), material name per triangle).
    Every face corner becomes its own vertex; faces with more than three corners are fanned into triangles."""
    pos, uvs, out_p, out_t, tris, mats = [], [], [], [], [], []
    current = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            w = line.split()
            if not w:
                continue
            if w[0] == "v":
                pos.append([float(x) for x in w[1:4]])
            elif w[0] == "vt":
                uvs.append([float(x) for x in w[1:3]])
            elif w[0] == "usemtl":
                current = w[1]
            elif w[0] == "f":
                ids = []
                for corner in w[1:]:
                    part = corner.split("/")
                    out_p.append(pos[int(part[0]) - 1])
                    has_uv = len(part) > 1 and part[1] != ""
                    out_t.append(uvs[int(part[1]) - 1] if has_uv else [0.0, 0.0])
                    ids.append(len(out_p) - 1)
                for k in range(1, len(ids) - 1):
                    tris.append([ids[0], ids[k], ids[k + 1]])
                    mats.append(current)
    return np.array(out_p, float), np.array(out_t, float), np.array(tris, np.int64), mats


def make_sampler(uv, tri_material, images, opaque):
    """-> the texel callback card_views.render_view wants: colour and alpha of each fragment from the nearest texel
    of its triangle's image. UVs wrap; image row 0 is the top, so V is flipped. opaque = ignore the image alpha."""
    names = sorted(set(tri_material), key=str)
    index = np.array([names.index(m) for m in tri_material])

    def texel(tri, corners, bary):
        rgb = np.full((len(tri), 3), GREY)
        alpha = np.full(len(tri), 255.0)
        fuv = (uv[corners] * bary[:, :, None]).sum(1)
        for k, name in enumerate(names):
            img = images.get(name)
            sel = index[tri] == k
            if img is None or not sel.any():
                continue
            h, w = img.shape[:2]
            x = np.floor((fuv[sel, 0] % 1.0) * w).astype(int).clip(0, w - 1)
            y = np.floor(((1.0 - fuv[sel, 1]) % 1.0) * h).astype(int).clip(0, h - 1)
            rgb[sel] = img[y, x, :3]
            if not opaque:
                alpha[sel] = img[y, x, 3]
        return rgb, alpha

    return texel


def view_list(cfg):
    """Config -> list of (azimuth degrees, elevation degrees). Either `rings` ([[elevation, count], ...], each ring a
    full turn) or `angles` views per entry of `elevations`. `span` is the arc the azimuths are spread over: 180 for
    shapes made of two-sided quads (cross, star), 360 for views picked by camera angle; default 180 up to 3 angles."""
    if cfg["rings"]:
        return [(360.0 * k / int(count), float(elev)) for elev, count in cfg["rings"] for k in range(int(count))]
    n = int(cfg["angles"])
    span = float(cfg["span"]) if cfg["span"] else (180.0 if n <= 3 else 360.0)
    return [(span * k / n, float(elev)) for elev in cfg["elevations"] for k in range(n)]


def view_axes(azimuth, elevation, left_handed=False):
    """One view's axes in the Z-up work frame -> (d, right, up): d points from the mesh to the camera, right / up are
    the image axes. Azimuth 0 has the camera on -Y; azimuth turns the camera towards +X.
    Right-handed frame: right = cross(up axis, d), so the camera on -Y sees +X on the image's right.
    Left-handed frame: the same numbers describe a mirrored world, so right = cross(d, up axis) (+X on the left);
    baking a left-handed mesh with the right-handed rule would give mirrored images."""
    a, e = math.radians(azimuth), math.radians(elevation)
    d = np.array([math.cos(e) * math.sin(a), -math.cos(e) * math.cos(a), math.sin(e)])
    if abs(elevation) > POLE_DEGREES:
        right = np.array([1.0, 0.0, 0.0])
    else:
        right = np.cross([0.0, 0.0, 1.0], d)
        right /= np.linalg.norm(right)
    if left_handed:
        right = -right
        return d, right, np.cross(right, d)
    return d, right, np.cross(d, right)


def atlas_layout(count, cell_w, cell_h, square):
    """Smallest power-of-two atlas that holds `count` cells -> (width, height, columns). Raises if none fits."""
    best = None
    side = 8
    sizes = []
    while side <= MAX_ATLAS_PX:
        sizes.append(side)
        side *= 2
    for w in sizes:
        for h in ([w] if square else sizes):
            fits = (w // cell_w) * (h // cell_h) >= count
            # smallest area wins; on a tie the wider atlas (rows of views read left to right)
            if fits and (best is None or w * h < best[0] * best[1] or (w * h == best[0] * best[1] and w > best[0])):
                best = (w, h, w // cell_w)
    if best is None:
        raise SystemExit("%d cells of %d x %d px do not fit a %d px atlas" % (count, cell_w, cell_h, MAX_ATLAS_PX))
    return best


def texture_bytes(width, height, vq):
    """Texture memory of an atlas: vector-quantised (codebook + 2 bits per texel) or 16 bits per texel."""
    return VQ_CODEBOOK_BYTES + width * height // 4 if vq else width * height * 2


def load_config(path):
    """Config file -> dict with defaults filled in and values checked; raises SystemExit with the reason."""
    with open(path, encoding="utf-8") as fh:
        cfg = dict(DEFAULTS, **json.load(fh))
    for key in ("obj", "out"):
        if key not in cfg:
            raise SystemExit("config key %s is missing" % key)
    unknown = set(cfg) - set(DEFAULTS) - {"obj", "out"}
    if unknown:
        raise SystemExit("unknown config keys: %s" % sorted(unknown))
    if cfg["up"] not in ("Z", "Y"):
        raise SystemExit('up must be "Z" or "Y"')
    if cfg["handedness"] not in ("right", "left"):
        raise SystemExit('handedness must be "right" or "left"')
    if cfg["alpha"] not in ("test", "opaque"):
        raise SystemExit('alpha must be "test" or "opaque"')
    if len(cfg["cell"]) != 2 or min(int(c) for c in cfg["cell"]) < 4:
        raise SystemExit("cell must be [width, height] in pixels, 4 or more each")
    if not cfg["rings"] and int(cfg["angles"]) < 1:
        raise SystemExit("angles must be 1 or more")
    return cfg


def to_caller_frame(vec, up):
    """A work-frame (Z-up) vector or point -> the caller's frame: unchanged for Z-up, (x, z, -y) for Y-up."""
    v = np.asarray(vec, float)
    return v if up == "Z" else np.array([v[0], v[2], -v[1]])


def run(cfg):
    """Bake every view of a checked config dict, write the atlas, the kit, the report and the optional preview OBJ.
    -> (kit dict, report dict), the contents of <out>.kit.json and <out>.report.json."""
    cell_w, cell_h = int(cfg["cell"][0]), int(cfg["cell"][1])
    pos, uv, tris, mats = read_obj(cfg["obj"])
    if len(tris) == 0:
        raise SystemExit("the OBJ has no faces")
    if cfg["up"] == "Y":                               # work Z-up: (x, y, z) -> (x, -z, y)
        pos = np.column_stack([pos[:, 0], -pos[:, 2], pos[:, 1]])
    images = {name: np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64)
              for name, path in cfg["textures"].items()}
    texel = make_sampler(uv, mats, images, cfg["alpha"] == "opaque")
    views = [(az, el) + view_axes(az, el, cfg["handedness"] == "left") for az, el in view_list(cfg)]
    lo, hi = pos.min(0), pos.max(0)
    centre = (lo + hi) / 2                             # the point every cell is centred on
    pc = pos - centre
    # One world size for every cell: the widest and the tallest the mesh gets in any view, plus the gutter.
    grow = 1 + 2 * float(cfg["gutter"])
    wide = max(float(np.abs(pc @ right).max()) for _az, _el, _d, right, _up in views) * grow
    tall = max(float(np.abs(pc @ up).max()) for _az, _el, _d, _right, up in views) * grow
    wide, tall = max(wide, 1e-6), max(tall, 1e-6)
    half = max(wide, tall)
    size = RENDER_SCALE * max(cell_w, cell_h)
    px_per_unit = size / (2 * half)
    atlas_w, atlas_h, cols = atlas_layout(len(views), cell_w, cell_h, bool(cfg["square_atlas"]))
    atlas = np.zeros((atlas_h, atlas_w, 4), np.uint8)
    means, kit_views = [], []
    for k, (az, el, d, right, up) in enumerate(views):
        img, mean_all = cv.render_view(pos, tris, texel, (d, right, up), size, centre, half)
        if mean_all is not None:
            means.append(mean_all)
        x0 = int(round(size / 2 - wide * px_per_unit))
        x1 = max(int(round(size / 2 + wide * px_per_unit)), x0 + 1)
        y0 = int(round(size / 2 - tall * px_per_unit))
        y1 = max(int(round(size / 2 + tall * px_per_unit)), y0 + 1)
        cx, cy = (k % cols) * cell_w, (k // cols) * cell_h
        atlas[cy:cy + cell_h, cx:cx + cell_w] = cv.shrink(img[y0:y1, x0:x1], cell_w, cell_h)
        # d = unit direction from the object to the camera; cell = [column, row] in the atlas grid, row 0 at the top
        kit_views.append({"d": [round(float(x), 6) + 0.0 for x in to_caller_frame(d, cfg["up"])],
                          "cell": [k % cols, k // cols]})
    gain = np.ones(3)
    drawn = atlas[..., 3] > 0
    if cfg["match_colour"] and means and drawn.any():
        gain = np.clip(np.mean(means, axis=0) / np.maximum(atlas[drawn][:, :3].astype(np.float64).mean(0), 1.0), *GAIN_RANGE)
        atlas[..., :3] = np.clip(atlas[..., :3] * gain, 0, 255).astype(np.uint8)
    atlas = cv.bleed(atlas, BLEED_STEPS)
    if cfg["alpha"] == "opaque":
        atlas[..., 3] = 255
    out = cfg["out"]
    with open(out + ".png", "xb") as fh:               # "x": never overwrite an existing file
        Image.fromarray(atlas, "RGBA").save(fh, "PNG")
    used = len(views) * cell_w * cell_h
    # anchor = the middle of every quad's bottom edge: a quad spans anchor +- half[0] sideways and
    # anchor .. anchor + 2 * half[1] along the up axis, so the middle of every cell is anchor + up * half[1].
    anchor = centre - np.array([0.0, 0.0, tall])
    kit = {"id": cfg["id"] or os.path.basename(out),
           "atlas": os.path.basename(out) + ".png",
           "cell_px": [cell_w, cell_h],
           "views": kit_views,
           "half": [round(wide, 5), round(tall, 5)],
           "anchor": [round(float(x), 5) for x in to_caller_frame(anchor, cfg["up"])],
           "alpha": cfg["alpha"],
           "handedness": cfg["handedness"]}
    report = {"source_triangles": int(len(tris)), "views": len(views),
              "atlas_px": [atlas_w, atlas_h],
              "texture_bytes": texture_bytes(atlas_w, atlas_h, bool(cfg["vq_texture"])),
              "atlas_fill": round(used / float(atlas_w * atlas_h), 3),
              "opaque_share": round(float(drawn.sum()) / used, 3),
              "texel_units": [round(2 * wide / cell_w, 5), round(2 * tall / cell_h, 5)],
              "quad_bytes": 4 * int(cfg["vertex_bytes"]) + 6 * int(cfg["index_bytes"]),
              "colour_gain": [round(float(g), 3) for g in gain]}
    with open(out + ".kit.json", "x", encoding="utf-8") as fh:
        json.dump(kit, fh, indent=2)
    with open(out + ".report.json", "x", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    level = [(k, v) for k, v in enumerate(views) if abs(v[1]) < 1e-6]
    if cfg["preview_obj"] and level:
        with open(out + ".obj", "x", encoding="utf-8") as fh:
            fh.write("# preview shape: one upright quad per level view, crossed at the kit centre; texture = the atlas\n")
            c = to_caller_frame(centre, cfg["up"])
            for k, (_az, _el, _d, right, up) in level:
                r, u = to_caller_frame(right, cfg["up"]) * wide, to_caller_frame(up, cfg["up"]) * tall
                cx, cy = (k % cols) * cell_w, (k // cols) * cell_h
                # atlas rectangle of this view's cell, V upwards
                u0, v0, u1, v1 = cx / atlas_w, 1 - (cy + cell_h) / atlas_h, (cx + cell_w) / atlas_w, 1 - cy / atlas_h
                for p, t in ((c - r - u, (u0, v0)), (c + r - u, (u1, v0)), (c + r + u, (u1, v1)), (c - r + u, (u0, v1))):
                    fh.write("v %.4f %.4f %.4f\nvt %.6f %.6f\n" % (p[0], p[1], p[2], t[0], t[1]))
            fh.write("usemtl card\n")
            for q in range(0, 4 * len(level), 4):
                fh.write("f %d/%d %d/%d %d/%d\n" % (q + 1, q + 1, q + 2, q + 2, q + 3, q + 3))
                fh.write("f %d/%d %d/%d %d/%d\n" % (q + 1, q + 1, q + 3, q + 3, q + 4, q + 4))
    return kit, report


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print("cutout:", json.dumps(run(load_config(sys.argv[1]))[1]))
