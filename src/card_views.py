"""Offline render math for far cards: orthographic z-buffered view of a triangle mesh with 1-bit alpha, colour
bleed into the transparent texels, and a box shrink that keeps edge colour. Pure numpy (+ PIL for the shrink).

Adapted from MIT-licensed code (see NOTICE): the rasteriser, the cell render, the bleed and the shrink.

Coordinates: mesh space, Z up. A view is (d, right, up): d = unit direction from the mesh towards the camera,
right / up = the image axes. Image row 0 is the top.
"""
import numpy as np

EDGE_EPSILON = 1e-7        # barycentric slack so a pixel centre exactly on a shared edge is not lost
MIN_AREA = 1e-12           # image-space triangle area below which the triangle is skipped (degenerate)
ALPHA_CUT = 128            # 0..255: fragments below this alpha are cut (1-bit alpha, as an alpha-tested draw)


def rasterize(xy, tris, size):
    """Pixel-centre coverage of every triangle, no culling.
    xy (V,2) image positions in pixels, tris (T,3) vertex indices, size = image side in pixels.
    -> (triangle index per fragment, pixel index per fragment (row * size + column), barycentrics (n,3))."""
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    lo = np.minimum(np.minimum(a, b), c)
    hi = np.maximum(np.maximum(a, b), c)
    x0 = np.clip(np.ceil(lo[:, 0] - 0.5), 0, size).astype(np.int64)
    y0 = np.clip(np.ceil(lo[:, 1] - 0.5), 0, size).astype(np.int64)
    x1 = np.clip(np.floor(hi[:, 0] - 0.5), -1, size - 1).astype(np.int64)
    y1 = np.clip(np.floor(hi[:, 1] - 0.5), -1, size - 1).astype(np.int64)
    w = np.maximum(x1 - x0 + 1, 0)
    h = np.maximum(y1 - y0 + 1, 0)
    count = np.where(np.abs(area) > MIN_AREA, w * h, 0)
    keep = np.nonzero(count)[0]
    tri = np.repeat(keep, count[keep])
    start = np.repeat(np.cumsum(count[keep]) - count[keep], count[keep])
    local = np.arange(len(tri)) - start
    px = x0[tri] + local % np.maximum(w[tri], 1)
    py = y0[tri] + local // np.maximum(w[tri], 1)
    fx, fy = px + 0.5, py + 0.5
    ta, tb, tc, ar = a[tri], b[tri], c[tri], area[tri]
    l0 = ((tb[:, 0] - fx) * (tc[:, 1] - fy) - (tc[:, 0] - fx) * (tb[:, 1] - fy)) / ar
    l1 = ((tc[:, 0] - fx) * (ta[:, 1] - fy) - (ta[:, 0] - fx) * (tc[:, 1] - fy)) / ar
    l2 = 1.0 - l0 - l1
    inside = (l0 >= -EDGE_EPSILON) & (l1 >= -EDGE_EPSILON) & (l2 >= -EDGE_EPSILON)
    return tri[inside], (py * size + px)[inside], np.stack([l0, l1, l2], 1)[inside]


def render_view(pos, tris, texel_fn, view, size, centre, half):
    """One orthographic view as RGBA uint8 (size x size); alpha is 0 or 255.
    pos (V,3) positions, tris (T,3) indices. texel_fn(triangle indices, corner indices (n,3), barycentrics (n,3))
    -> (rgb (n,3) float 0..255, alpha (n,) 0..255) for the n fragments. A fragment under ALPHA_CUT is dropped; of the
    rest the one nearest the camera wins its pixel. centre = mesh-space point shown in the image middle,
    half = half the image's size in mesh units.
    Also returns the mean colour of ALL surviving fragments (hidden ones too), used for the colour match."""
    d, right, up = view
    scale = size / (2.0 * half)
    out = np.zeros((size * size, 4), np.uint8)
    pc = pos - centre
    xy = np.stack([pc @ right * scale + size / 2, size / 2 - pc @ up * scale], 1)
    z = pc @ d
    tri, pix, bary = rasterize(xy, tris, size)
    mean_all = None
    if len(tri):
        corners = tris[tri]
        zf = (z[corners] * bary).sum(1)
        rgb, alpha = texel_fn(tri, corners, bary)
        ok = alpha >= ALPHA_CUT
        pix, zf, rgb = pix[ok], zf[ok], rgb[ok]
        if len(pix):
            mean_all = rgb.mean(0)
            order = np.lexsort((-zf, pix))         # per pixel: nearest to the camera first
            first = np.ones(len(order), bool)
            first[1:] = pix[order][1:] != pix[order][:-1]
            win = order[first]
            out[pix[win], :3] = np.clip(rgb[win], 0, 255).astype(np.uint8)
            out[pix[win], 3] = 255
    return out.reshape(size, size, 4), mean_all


def bleed(rgba, steps):
    """Spread colour into transparent texels (their alpha stays 0), `steps` texels deep, so texture filtering does
    not pull dark colour in at the cut edge. -> a new RGBA uint8 array."""
    rgb = rgba[..., :3].astype(np.float64)
    known = rgba[..., 3] > 0
    for _ in range(steps):
        acc = np.zeros_like(rgb)
        cnt = np.zeros(known.shape)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            k = np.roll(known, (dy, dx), (0, 1))
            acc += np.roll(rgb, (dy, dx), (0, 1)) * k[..., None]
            cnt += k
        grow = ~known & (cnt > 0)
        rgb[grow] = acc[grow] / cnt[grow][:, None]
        known = known | grow
    out = rgba.copy()
    out[..., :3] = rgb.astype(np.uint8)
    return out


def shrink(rgba, w, h):
    """Box shrink of an RGBA uint8 array to w x h. Colour is weighted by alpha (so cut texels do not darken the
    edge), then alpha becomes 1-bit at half coverage. -> RGBA uint8 (h, w, 4)."""
    from PIL import Image
    a = rgba.astype(np.float64) / 255.0
    pm = a.copy()
    pm[..., :3] *= pm[..., 3:4]
    small = Image.fromarray((pm * 255).round().astype(np.uint8), "RGBA").resize((w, h), Image.BOX)
    s = np.asarray(small).astype(np.float64) / 255.0
    al = s[..., 3:4]
    rgb = np.where(al > 0, s[..., :3] / np.maximum(al, 1e-6), 0)
    out = np.zeros((h, w, 4), np.uint8)
    out[..., :3] = (rgb.clip(0, 1) * 255).round()
    out[..., 3] = np.where(al[..., 0] >= 0.5, 255, 0)
    return out
