"""Tests for cutout. Run from the repository root:  python -B -m unittest discover -s tests
Every test builds its own synthetic input in a fresh temporary folder."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))
import card_views as cv                                # noqa: E402
import cutout                                          # noqa: E402
import make_example                                    # noqa: E402


def flat_texel(tri, corners, bary):
    """Texel callback for the render tests: every fragment mid grey and opaque."""
    return np.full((len(tri), 3), 200.0), np.full(len(tri), 255.0)


class RenderTests(unittest.TestCase):
    def test_square_fills_its_view(self):
        """A unit square seen straight on, in a view exactly its size, covers every pixel."""
        pos = np.array([[-0.5, 0, -0.5], [0.5, 0, -0.5], [0.5, 0, 0.5], [-0.5, 0, 0.5]], float)
        tris = np.array([[0, 1, 2], [0, 2, 3]])
        img, mean = cv.render_view(pos, tris, flat_texel, cutout.view_axes(0.0, 0.0), 16, np.zeros(3), 0.5)
        self.assertTrue((img[..., 3] == 255).all())
        self.assertAlmostEqual(float(mean[0]), 200.0)

    def test_bleed_keeps_alpha(self):
        """Bleed changes colour of clear texels only; alpha is untouched."""
        img = np.zeros((8, 8, 4), np.uint8)
        img[4, 4] = (90, 90, 90, 255)
        out = cv.bleed(img, 2)
        self.assertTrue((out[..., 3] == img[..., 3]).all())
        self.assertEqual(int(out[4, 5, 0]), 90)

    def test_view_axes_are_a_frame(self):
        """Every view's three axes are unit length and at right angles, the pole views included."""
        for az, el in ((0, 0), (90, 0), (45, 35), (10, 90), (200, -90)):
            d, right, up = cutout.view_axes(az, el)
            for a, b in ((d, right), (d, up), (right, up)):
                self.assertAlmostEqual(float(a @ b), 0.0, places=6)
            for a in (d, right, up):
                self.assertAlmostEqual(float(np.linalg.norm(a)), 1.0, places=6)


class LayoutTests(unittest.TestCase):
    def test_atlas_sizes(self):
        """The layout search gives the smallest square power-of-two atlas for a view count."""
        self.assertEqual(cutout.atlas_layout(2, 64, 64, True)[:2], (128, 128))
        self.assertEqual(cutout.atlas_layout(16, 64, 64, True)[:2], (256, 256))
        self.assertEqual(cutout.atlas_layout(58, 64, 64, True)[:2], (512, 512))
        self.assertEqual(cutout.atlas_layout(2, 64, 64, False)[:2], (128, 64))

    def test_view_counts(self):
        """`angles` x `elevations` and `rings` both give the expected number of views and the default arc."""
        base = dict(cutout.DEFAULTS)
        self.assertEqual([a for a, _e in cutout.view_list(dict(base, angles=2))], [0.0, 90.0])
        self.assertEqual([a for a, _e in cutout.view_list(dict(base, angles=3))], [0.0, 60.0, 120.0])
        self.assertEqual(len(cutout.view_list(dict(base, angles=8, elevations=[0, 35]))), 16)
        rings = [[90, 1], [65, 8], [35, 12], [0, 16], [-35, 12], [-65, 8], [-90, 1]]
        self.assertEqual(len(cutout.view_list(dict(base, rings=rings))), 58)


class BakeTests(unittest.TestCase):
    def bake(self, **extra):
        """Bake the example bush into a new temporary folder; -> (folder, kit, report)."""
        folder = os.path.join(tempfile.mkdtemp(), "run")
        os.makedirs(folder)
        make_example.bush_obj(os.path.join(folder, "bush.obj"))
        Image.fromarray(make_example.leaf_texture(), "RGBA").save(os.path.join(folder, "leaf.png"))
        cfg = {"obj": os.path.join(folder, "bush.obj"), "textures": {"leaf": os.path.join(folder, "leaf.png")},
               "out": os.path.join(folder, "card")}
        cfg.update(extra)
        path = os.path.join(folder, "config.json")
        with open(path, "x", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        kit, report = cutout.run(cutout.load_config(path))
        return folder, kit, report

    def test_cross_kit(self):
        """Two angles: exactly the contract keys, two views with unit directions, a 128 x 128 atlas, 4 triangles."""
        folder, kit, report = self.bake()
        self.assertEqual(set(kit), {"id", "atlas", "cell_px", "views", "half", "anchor", "alpha", "handedness"})
        self.assertEqual((kit["id"], kit["atlas"], kit["alpha"]), ("card", "card.png", "test"))
        self.assertEqual([v["cell"] for v in kit["views"]], [[0, 0], [1, 0]])
        self.assertEqual(kit["views"][0]["d"], [0.0, -1.0, 0.0])
        self.assertEqual(report["atlas_px"], [128, 128])
        self.assertEqual(report["texture_bytes"], 2048 + 128 * 128 // 4)
        _pos, _uv, tris, mats = cutout.read_obj(os.path.join(folder, "card.obj"))
        self.assertEqual((len(tris), set(mats)), (4, {"card"}))
        with open(os.path.join(folder, "card.kit.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), kit)

    def test_cut_and_opaque_alpha(self):
        """Cut alpha is only 0 or 255 with some of each; opaque alpha is 255 everywhere."""
        folder, _kit, report = self.bake(angles=8)
        img = np.asarray(Image.open(os.path.join(folder, "card.png")))
        self.assertEqual(set(np.unique(img[..., 3])), {0, 255})
        self.assertTrue(0.05 < report["opaque_share"] < 0.95)
        folder, _kit, _report = self.bake(alpha="opaque")
        self.assertTrue((np.asarray(Image.open(os.path.join(folder, "card.png")))[..., 3] == 255).all())

    def test_y_up_swaps_axes(self):
        """A Y-up bake keeps level view directions level in the caller's frame (no Y part)."""
        _folder, kit, _report = self.bake(up="Y")
        self.assertAlmostEqual(kit["views"][0]["d"][1], 0.0)
        self.assertAlmostEqual(abs(kit["views"][0]["d"][2]), 1.0)

    def bake_flag(self, handedness):
        """Bake a mirror-detecting mesh (a full-width base strip plus one square in the +X top corner of the XZ
        plane) as a single opaque-textured view; -> the atlas alpha as a bool array, row 0 at the top."""
        folder = os.path.join(tempfile.mkdtemp(), "flag")
        os.makedirs(folder)
        with open(os.path.join(folder, "flag.obj"), "x", encoding="utf-8") as fh:
            fh.write("v -1 0 0\nv 1 0 0\nv 1 0 0.2\nv -1 0 0.2\n")       # base strip, keeps the box symmetric
            fh.write("v 0.5 0 1.5\nv 1 0 1.5\nv 1 0 2\nv 0.5 0 2\n")     # the mark: +X side, top
            fh.write("f 1 2 3 4\nf 5 6 7 8\n")
        path = os.path.join(folder, "config.json")
        with open(path, "x", encoding="utf-8") as fh:
            json.dump({"obj": os.path.join(folder, "flag.obj"), "out": os.path.join(folder, "flag_card"), "angles": 1,
                       "gutter": 0.0, "handedness": handedness}, fh)
        kit, _report = cutout.run(cutout.load_config(path))
        self.assertEqual(kit["handedness"], handedness)
        return np.asarray(Image.open(os.path.join(folder, "flag_card.png")))[..., 3] > 0

    def test_handedness_mirrors_the_image(self):
        """Right-handed: the +X mark is in the image's top right. Left-handed: top left. Never both."""
        right = self.bake_flag("right")
        left = self.bake_flag("left")
        h, w = right.shape
        self.assertTrue(right[: h // 4, 3 * w // 4:].any())
        self.assertFalse(right[: h // 4, : w // 4].any())
        self.assertTrue(left[: h // 4, : w // 4].any())
        self.assertFalse(left[: h // 4, 3 * w // 4:].any())

    def test_never_overwrites(self):
        """A second run onto the same output stem fails instead of replacing files."""
        folder, _kit, _report = self.bake()
        with self.assertRaises(FileExistsError):
            cutout.run(cutout.load_config(os.path.join(folder, "config.json")))

    def test_bad_config_is_refused(self):
        """Unknown key, bad alpha word and zero angles are all refused."""
        folder = tempfile.mkdtemp()
        for bad in ({"nonsense": 1}, {"alpha": "blend"}, {"angles": 0}, {"handedness": "up"}):
            path = os.path.join(folder, "c%d.json" % len(os.listdir(folder)))
            with open(path, "x", encoding="utf-8") as fh:
                json.dump(dict({"obj": "a", "out": "b"}, **bad), fh)
            with self.assertRaises(SystemExit):
                cutout.load_config(path)


if __name__ == "__main__":
    unittest.main()
