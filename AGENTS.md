# Notes for AI agents using Cutout

What it does: one textured OBJ in; one atlas of baked views, a kit JSON for the engine, a cost report and an
optional crossed-quad preview OBJ out. It only bakes. `README.md` lists every config key, the kit fields and the
display contract (shapes and modes the engine chooses from).

## Commands

    python -B src/cutout.py <config.json>                      # bake
    python -B examples/make_example.py <new folder> [angles]   # synthetic bush, to see a working run
    python -B -m unittest discover -s tests                    # must pass before and after any change

## Minimal configs

Cross (two quads), the usual far tier for a plant:

    {"obj": "in/tree.obj", "textures": {"bark": "in/bark.png", "leaf": "in/leaf.png"}, "out": "out/tree_cross"}

Ring of 8 for a view-dependent billboard, taller cells:

    {"obj": "in/tree.obj", "textures": {"leaf": "in/leaf.png"}, "out": "out/tree_ring8", "angles": 8, "cell": [64, 128]}

The keys of `textures` are the OBJ's `usemtl` names, exactly.

## Choosing angles

- 1: flat things seen from one side, or a camera-facing billboard of a roughly round object.
- 2 (cross) or 3 (star): fixed in place, no runtime code, fine for bushes and distant trees.
- 8 or 16 with span 360: objects that are clearly different from each side and are shown view-dependent.
- `rings`: only when the camera can look down on the object from high up.

More views cost texture memory, not vertices: check `texture_bytes` in the report against your budget.

## Check after every bake

1. Open `<out>.png` and look at it. Every cell must show the object, upright, filling most of the cell.
2. `opaque_share` between about 0.15 and 0.9. Near 0 = nothing was drawn (wrong `up`, no faces, UVs missing).
   Near 1 with `"test"` alpha = the textures have no alpha where you expected some.
3. `colour_gain` close to 1. A value pinned at 0.8 or 1.25 means the bake and the mesh differ a lot in colour:
   check that the right image is mapped to each material.
4. `atlas_fill` low (under 0.5) means wasted texture: change `angles` or `cell` so the grid fills the atlas.
5. The replacement must be cheaper than the mesh in both triangles and vertices (2 triangles, 4 vertices per
   quad). Do not replace a mesh that is already that small.
6. Look at the result at the distance it will be seen. `texel_units` tells you the distance where one texel is
   about one pixel; nearer than that it will look soft.

## Pitfalls

- Outputs are never overwritten. A second run with the same `out` fails; choose a new stem.
- OBJ UVs are V upwards. If the bake looks vertically mirrored, the source UVs were V downwards: flip them first.
- `up` matters: a Y-up mesh baked as Z-up gives images of the object lying on its side.
- `handedness` matters: a mesh from a left-handed frame baked as right-handed gives images mirrored left to right.
  Symmetric plants hide this; test once with an asymmetric object.
- Split a mesh that contains several separate objects and bake each on its own.
- `span` 180 is for crossed two-sided quads; a view-dependent ring needs 360 (the default from 4 angles up).
- The kit's `views[i].cell` row 0 is the TOP row of the image; convert to your engine's V direction.
- Keep this repository generic: OBJ, PNG and JSON in and out. Do not add project paths, asset names from any
  product, or one engine's binary formats.
