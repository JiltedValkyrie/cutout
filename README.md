# Cutout

Bakes images of a textured mesh from a set of angles you choose into one atlas, and writes a small JSON **kit**
that tells an engine where each image is and how big the object is.

It is for replacing detailed plants, trees, rocks and clutter with a few textured quads on hardware where vertices
are expensive and alpha-tested quads are cheap, such as tile-deferred renderers (the Dreamcast's PowerVR among
them). A 4,000 triangle tree becomes 2 quads and one 128 x 128 texture.

Cutout only bakes. How the images are shown (always facing the camera, picked by view angle, crossed quads fixed
in place) is the engine's choice per placement; the kit is the contract between the two. See "Display contract".

## Install

Python 3.9 or newer, with `numpy` and `Pillow`:

    pip install numpy Pillow

No build step. Run it from a clone of this repository.

## Use

    python -B src/cutout.py config.json

| Config key | Meaning | Default |
|---|---|---|
| `obj` | input OBJ: positions, UVs (V upwards), faces grouped by `usemtl` | required |
| `out` | output path stem; files are written beside it and must not exist yet | required |
| `textures` | `{material name: image path}`; a material with no entry is drawn flat grey | `{}` |
| `angles` | views per elevation row, spread evenly round the up axis | `2` |
| `span` | arc in degrees the angles cover: 180 for crossed two-sided quads, 360 for views picked by camera angle | 180 up to 3 angles, else 360 |
| `elevations` | list of elevation rows in degrees (0 = level, 90 = straight down on the object) | `[0]` |
| `rings` | `[[elevation, count], ...]`, each a full turn; replaces `angles`, `span` and `elevations` | none |
| `cell` | `[width, height]` in pixels of one view | `[64, 64]` |
| `square_atlas` | force a square atlas (some texture formats need it) | `true` |
| `alpha` | `"test"` = 1-bit alpha cut at one half, `"opaque"` = no alpha | `"test"` |
| `id` | name written into the kit | the output file name |
| `up` | up axis of the mesh, `"Z"` or `"Y"` | `"Z"` |
| `handedness` | `"right"` or `"left"`: handedness of the mesh's coordinate frame (see the kit section) | `"right"` |
| `gutter` | empty margin round the mesh, as a share of its size | `0.02` |
| `match_colour` | scale the images so their mean colour equals the mesh's mean colour | `true` |
| `preview_obj` | also write `<out>.obj`: one upright quad per level view, crossed | `true` |
| `vertex_bytes`, `index_bytes` | sizes in your engine, used only for the report | `24`, `2` |
| `vq_texture` | report texture bytes as vector-quantised (2,048 + texels / 4); `false` = 16 bits per texel | `true` |

Common choices of `angles`: 1 = single quad, 2 = cross, 3 = star, 4 / 8 / 16 = a ring for views picked by camera
angle. A full sphere of 58 views: `"rings": [[90,1],[65,8],[35,12],[0,16],[-35,12],[-65,8],[-90,1]]`.

Atlas size and vector-quantised texture memory, square atlas:

| Views | 64 px cells | 128 px cells |
|---|---|---|
| 1 | 64 x 64, 3 KB | 128 x 128, 6 KB |
| 2, 3, 4 | 128 x 128, 6 KB | 256 x 256, 18 KB |
| 8, 16 | 256 x 256, 18 KB | 512 x 512, 66 KB |
| 58 | 512 x 512, 66 KB | 1024 x 1024, 258 KB |

## Output

- `<out>.png`: the atlas, RGBA. Cells fill it left to right, top to bottom, in view order. With `"test"` alpha the
  alpha channel is only 0 or 255, and colour is bled into the clear texels so filtering does not darken edges.
- `<out>.kit.json`: the kit (below).
- `<out>.report.json`: `source_triangles`, `views`, `atlas_px`, `texture_bytes`, `atlas_fill` (share of the atlas
  holding cells), `opaque_share` (share of the cells that is drawn), `texel_units` (width and height of one texel
  in mesh units), `quad_bytes` (one quad in your engine), `colour_gain` (what the colour match applied).
- `<out>.obj` (optional): a ready crossed-quad mesh using the atlas, material `card`, for engines that just want a
  static mesh and for looking at the result in any viewer.

A fair distance to swap a mesh for its baked quads is where one texel covers about one screen pixel.

## The kit

    {
      "id": "bush_card",
      "atlas": "bush_card.png",
      "cell_px": [64, 64],
      "views": [ {"d": [0.0, -1.0, 0.0], "cell": [0, 0]},
                 {"d": [1.0,  0.0, 0.0], "cell": [1, 0]} ],
      "half": [1.007, 0.471],
      "anchor": [-0.004, 0.042, -0.018],
      "alpha": "test",
      "handedness": "right"
    }

- `views[i].d`: unit direction from the object towards the camera that view was baked from, in the mesh's frame.
- `views[i].cell`: `[column, row]` of the view's cell in the atlas grid; row 0 is the top row. The cell's pixel
  rectangle is `column * cell_px[0], row * cell_px[1]`, size `cell_px`.
- `half`: `[half width, half height]` of every view's quad, in mesh units. All views share it.
- `anchor`: the middle of every quad's bottom edge, in the mesh's frame. A level view's quad spans
  `anchor - right * half[0]` to `anchor + right * half[0]`, and from `anchor` to `anchor + up * 2 * half[1]`.
- `handedness`: `"right"` or `"left"`, copied from the config. It fixes the image axes of a view:
  - right-handed frame: `right = normalize(cross(up_axis, d))`, `up = cross(d, right)`
  - left-handed frame: `right = normalize(cross(d, up_axis))`, `up = cross(right, d)` (the mirrored sign)
  - for a view straight down or up (`d` along the up axis), `right` is the mesh's +X (right-handed) or -X (left).
  Bake a mesh with the handedness of the frame its numbers are in. With the wrong one every image is mirrored
  left to right: check with any asymmetric object (lettering, a one-sided branch).
- `alpha`: `"test"` or `"opaque"`.
- Tilted views (elevation not 0) use the same `half` as level views: every cell, level or tilted, is one quad of
  `2 * half[0]` by `2 * half[1]`. A tilted cell is centred on `anchor + up_axis * half[1]` and is drawn
  screen-aligned, its sides along that view's own `right` and `up`. Fixed and axis-locked shapes use level views
  only; tilted cells are for the view-dependent mode.

## Display contract

The engine or level editor chooses, per placement, a **shape** and a **mode**. Cutout does not; it only has to have
baked the views they need.

| Shape | Needs | Draws |
|---|---|---|
| single | 1 view | one quad |
| cross | 2 views, span 180 | two quads at right angles through the anchor's up axis |
| star | 3 views, span 180 | three quads 60 degrees apart |

| Mode | What the runtime must do |
|---|---|
| fixed | place the quads once from the kit's `d` vectors; never rotate. Works as an ordinary static mesh (the preview OBJ is exactly this). |
| axis-locked | rotate a single quad round the up axis so it faces the camera's position; use view 0, or the view whose `d` is nearest the camera direction projected flat. |
| camera-facing | turn a single quad fully towards the camera (billboard); use view 0. |
| view-dependent | each frame pick the view whose `d` has the largest dot product with the object-to-camera direction (in the object's frame), and draw that cell on a camera-facing quad. Needs a 360 ring, or `rings` for views from above and below. |

## Example

    python -B examples/make_example.py my_example        # cross (2 views)
    python -B examples/make_example.py my_example_8 8    # ring of 8

builds a small synthetic bush and its leaf texture in the folder, then bakes it.

## Tests

    python -B -m unittest discover -s tests

## Limits

- Baked quads are for distance or for small background objects. Close up they are blurry; a crossed tree shows
  its trunk twice when seen on the diagonal.
- One mesh that holds two separate objects smears them together; bake them separately.
- Lighting is not baked: the images carry texture colour only, and the engine lights or tints them.
- OBJ input only; faces need UVs for a textured result. Animated meshes are baked in the pose of the file.
- No mip maps and no texture compression are written; the atlas is a plain PNG for your own texture pipeline.

## Licence and credit

MIT, see `LICENSE`. The offline render in `src/card_views.py` is adapted from MIT-licensed code; see `NOTICE`.
