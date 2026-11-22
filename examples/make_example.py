"""Makes a small synthetic bush (leaf-shaped quads round a stem line, one generated leaf texture) and bakes it.
Nothing here comes from any game or asset pack: the mesh and the texture are computed below.

usage: python -B examples/make_example.py <new output folder> [angles]
writes bush.obj, leaf.png, config.json, then bush_card.png / .kit.json / .report.json / .obj
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import cutout                                          # noqa: E402

LEAVES = 14            # leaf quads round the stem
LEAF_LEN = 1.0         # mesh units: length of one leaf
LEAF_WIDTH = 0.45      # mesh units: width of one leaf
TEXTURE_PX = 64        # px: side of the generated leaf texture


def leaf_texture():
    """-> RGBA uint8 (TEXTURE_PX, TEXTURE_PX, 4): a green pointed leaf with a lighter midrib, clear outside it."""
    v, u = np.mgrid[0:TEXTURE_PX, 0:TEXTURE_PX] / (TEXTURE_PX - 1.0)
    inside = np.abs(u - 0.5) < np.sin(np.pi * v) * 0.45    # the leaf outline: widest in the middle rows
    img = np.zeros((TEXTURE_PX, TEXTURE_PX, 4), np.uint8)
    img[..., 0] = 40 + 30 * v
    img[..., 1] = 110 + 60 * v
    img[..., 2] = 35
    img[np.abs(u - 0.5) < 0.03, 1] = 200               # midrib
    img[..., 3] = np.where(inside, 255, 0)
    return img


def bush_obj(path):
    """Write the bush: LEAVES quads fanned round the Z axis, each tilted up by a different amount. Material `leaf`."""
    with open(path, "x", encoding="utf-8") as fh:
        fh.write("vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n")
        for k in range(LEAVES):
            turn = 2 * math.pi * k / LEAVES
            tilt = math.radians(25 + 40 * (k % 3) / 2)
            out = np.array([math.cos(turn) * math.cos(tilt), math.sin(turn) * math.cos(tilt), math.sin(tilt)])
            side = np.array([-math.sin(turn), math.cos(turn), 0.0]) * LEAF_WIDTH / 2
            for corner in (-side, side, out * LEAF_LEN + side, out * LEAF_LEN - side):
                fh.write("v %.5f %.5f %.5f\n" % tuple(corner))
        fh.write("usemtl leaf\n")
        for k in range(LEAVES):
            b = 4 * k
            fh.write("f %d/1 %d/2 %d/3 %d/4\n" % (b + 1, b + 2, b + 3, b + 4))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    folder = sys.argv[1]
    os.makedirs(folder, exist_ok=False)
    bush_obj(os.path.join(folder, "bush.obj"))
    Image.fromarray(leaf_texture(), "RGBA").save(os.path.join(folder, "leaf.png"))
    config = {"obj": os.path.join(folder, "bush.obj"), "textures": {"leaf": os.path.join(folder, "leaf.png")},
              "out": os.path.join(folder, "bush_card"), "angles": int(sys.argv[2]) if len(sys.argv) == 3 else 2}
    with open(os.path.join(folder, "config.json"), "x", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
    print("cutout example:", json.dumps(cutout.run(cutout.load_config(os.path.join(folder, "config.json")))[1]))
