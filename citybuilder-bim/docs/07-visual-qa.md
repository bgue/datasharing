# 07 Visual QA (WP-R)

Real screenshots of the real game scene, looked at one by one, with the layout and camera defects they showed fixed. The
harness is `godot/tools/screenshot.gd`, the standard set is `godot/tools/shots.sh`, the images are in `docs/img/`. How to run
them: `godot/README.md`, section "Screenshots and visual QA".

## Method

* Software OpenGL 3 (llvmpipe) under Xvfb. Every image is `get_viewport().get_texture().get_image()` of the full window, HUD
  included, saved as PNG after the meshes and UI have settled (12 process frames plus two `frame_post_draw`).
* Scenario state comes from the game itself: `Planner.auto_layout` (haul roads, laydown, cranes), then `Planner.autopilot`
  week by week, events resolved with choice 0. So "week 15" is a plausible mid-build state, not a staged one.
* Two window sizes are covered: **1280x720** (the tight case, every image) and **1920x1080** (two images). Overviews hide the
  timeline (`--hide=gantt`) so the 3D view can be judged; the timeline has its own images at both sizes.
* PNGs above 400 KB are reduced to a 256-colour palette by `shots.sh` (the flat-shaded frames do not show it). All 30 images of
  this set are 106 to 340 KB (the two 1920x1080 images went through the palette step and are 152 KB each).
* The first pass of images showed the problems listed below; the images in `docs/img/` are the final ones after the fixes
  (earlier images were replaced, not kept).

## Defects found and fixed

| # | Defect (how it showed) | Cause | Fix |
| --- | --- | --- | --- |
| 1 | The zone inspector ran off the right screen edge, the hint bar was 1220 px wide, the event toast about 1060 px, the timeline 712 px tall (below the screen), the score panel stretched to the bottom-right corner | `UiStyle.place` took the anchors as a `Rect2` and used `Rect2.end` (position + size), so right and bottom anchors were 1 or 2 instead of the intended values | `UiStyle.place` now reads (left, top, right, bottom) anchors as documented; every panel is placed explicitly by `main.gd` |
| 2 | Top bar cut off at 1280 px: "Export" and "Menu" were not visible (minimum width 1361 px) | Fixed label minimums (title 150, cash 250), long mode text, 8 px button margins | Mode text "Build mode" / "Assign mode", cash "cash / budget" with tooltip, button margin 6, title 200 px with ellipsis: 1156 px minimum plus the wider title, fits 1280 |
| 3 | Inspector, procurement, charts, crews, hint bar and the timeline overlapped each other (procurement and charts were drawn under the timeline) | Each panel was anchored on its own with guessed offsets | Left dock (crews + charts) and right dock (inspector + procurement) are containers between the top bar and the timeline; the hint bar sits at the bottom of the free middle; nothing overlaps at 1280x720 or 1920x1080 |
| 4 | Crew panel 327 px wide, the 270 px column was exceeded; crew names pushed the "+ / -" buttons | Long labels and an unwrapped help line set the minimum width | Labels clip with ellipsis, help line wraps, panel minimum 250 |
| 5 | Empty zone inspector took the whole right column; procurement showed one row | Both expanded or had large minimum sizes | Empty inspector shrinks to its header, procurement expands into the rest (stretch ratio 2.5 : 1 once a zone is shown), inspector minimums lowered, the one-zone lane view yields to the open timeline |
| 6 | Event toast hid the storey badge and the weekly report overlapped the toast; the weekly report had 9 wrapped lines and clipped | Both anchored in the same area, report text with a fixed height | Toast centred under the badge, weekly report at the right end of the free area above the hint bar, 6 truncated lines, fit-to-content |
| 7 | Sequence editor: minimum height above the screen, header covered by the storey badge, chain table with a horizontal scrollbar and cut headers, 3D view showing through the tables, hint bar drawn over it | Column minimums summed to more than the width, transparent tree backgrounds, fixed anchors | Editor is placed in the free middle (right-aligned to the right dock, at most 1100 px), the left dock yields when the screen is too narrow, the timeline folds away on screens shorter than 900 px while the editor is open (and returns on close), tables on an opaque ground, chain 440 px minimum with narrower columns, editor ends above the hint bar, badge hidden while the editor is open |
| 8 | Marker legend inside the editor was a 9-row column that squeezed the chain table to nothing | Vertical list in a panel with no spare height | Chips wrap in a strip (2 rows at 880 px) |
| 9 | "What's needed?" dialog 900 x 4606 px (buttons off screen), light grey engine frame | Autowrap labels with no width reported a huge height on the first layout, default Window / AcceptDialog theme | Label minimum widths, `popup_centered_clamped`, smaller minimum sizes, dark AcceptDialog and Window frame in `UiStyle.make_theme` |
| 10 | Whole site did not fit at the start, centred behind the panels, long thin sites (civil) tiny on the diagonal | `frame_site` used `extent * 2.2`, ignored the aspect ratio, the HUD and the camera yaw | `view.gd`: exact perspective fit of the site box into the area not covered by the HUD (`fit_zoom`, `set_insets`), centred in that area, yaw (default, +-45, +90 degrees) chosen when it fits clearly better (civil), `frame_cells` for zones and installations, `snap` for first frames; rotating with the mouse keeps the player's yaw |
| 11 | Gantt bar names were dark text on dark bars | Fixed near-black text colour | Light text with a dark outline, 11 px |
| 12 | Blank line at the top of the hint bar | The message label took space while empty | Hidden while empty |
| 13 | No cue which storey has the focus | Only the yellow name in the top bar tracker | Badge "Storey focus: <name> (PgUp / PgDn)" and, above the lowest storey, a translucent plane with a blue rim at the floor of the focused storey |
| 14 | Final score panel as tall as its fixed 460 px frame, translucent over the 3D view | Fixed offsets, panel background alpha 0.84 | Sized by content, centred, 97 % opaque ground, duplicate reason line removed |
| 15 | Zone hover and inspector content depended on where the mouse pointer was parked | Pointer parked mid-screen under Xvfb hovered a zone | The harness parks the pointer in the corner |

Tests adapted: `tests/test_gantt.gd` (docks instead of per-panel offsets, lane yields to the open timeline). No simulation code was
touched.

## Screenshots

Common to all overview images at 1280x720: top bar complete (week, cash / budget, speed, panel buttons, per-storey phase
tracker), crews and spend chart on the left, zone inspector and procurement on the right, hint bar at the bottom centre,
storey badge top left of the 3D area, whole site visible inside the free middle. The table lists only what is specific.

### Overviews (timeline hidden)

| Image | Observations | Fixed / handed over |
| --- | --- | --- |
| `minimal_overview_w0` | Small site, building and two hint tiles clear, zones as green pads, hint text legible | Fits with room to spare; the site could be drawn larger but the zoom floor (15) keeps the Kenney look |
| `minimal_overview_w15`, `_w30` | The scenario ends in week 5: the final score panel is shown (handover, grade S), table legible and centred | Panel size and opacity (14) |
| `healthcare_standard_overview_w0` | All elements ghosted, 18 zone pads, the live-ward block (bottom) strongly coloured and readable, crane reach discs | Yaw turned to align with the grid (wide site); handed over: ghost white fog |
| `healthcare_standard_overview_w15` | Tracker shows ground in progress, procurement list shows the late items in red, hint bar message of the week | Procurement now uses the free right column |
| `healthcare_standard_overview_w30` | Cash is negative (red in the top bar), steel and blue modules visible, level 1 and 2 tracker boxes progressing | None |
| `industrial_standard_overview_w0` | Large pipe-rack and module grid appears as a faint ghost lattice with many worker figures at the gate; site fills the middle | Handed over: ghost lattice hard to read |
| `industrial_standard_overview_w15`, `_w30` | Big white ghost slabs (process hall) dominate; the tracker has a single storey row; "Incident" message shows in the hint bar | Handed over: white slabs |
| `civil_standard_overview_w0`, `_w15` | Long thin site: turned so the road runs up the screen and fills the area (before: a small diagonal sliver); focus plane rim visible at the road level | Camera yaw choice (10), focus plane (13) |
| `civil_standard_overview_w30` | The autopilot is bankrupt in week 26 (known gap in the scale test): score panel "Level failed, Bankrupt" with the reason line | Panel (14) |
| `healthcare_manual_demo_overview_w0`, `_w15`, `_w30` | Same site as healthcare_standard; the top bar title is cut with an ellipsis ("Hospital wing (manual ...") | Title ellipsis (2); tooltip shows the full name |

### Panels and views

| Image | Observations | Fixed / handed over |
| --- | --- | --- |
| `healthcare_standard_overview_w15_1920` | Default state at 1920x1080 (timeline open at 30 %): docks keep their width, the 3D view gets the middle, bars in the timeline readable with names | Layout scales; the docks could be wider on large screens |
| `healthcare_standard_overview_w15_gantt` | Timeline open at 720p: header, zoom buttons, filters complete; the model, hint bar, crews, charts, inspector and procurement all sit above it | 3 (before: charts, hint bar and procurement overlapped the timeline) |
| `healthcare_standard_overview_w15_gantt_1920` | Four zone rows visible, bar labels light on dark, delivery diamonds and the red contract line readable | 11 |
| `healthcare_standard_overview_w15_editor` | Sequence editor for zone 3 fills the free middle, left dock folded away, timeline folded away; palette, chain with 8 columns without a scrollbar, elements, lane, hint bar below | 7 (before: minimum height off screen, headers cut) |
| `healthcare_manual_demo_overview_w15_editor_legend` | Manual mode on, six tasks of the demo chain with their glyphs (S for survey), legend as a two-row strip at the bottom, hint bar visible | 7, 8 |
| `healthcare_standard_overview_w15_whats_needed` | Dialog centred, 900x640, dark frame, table with status chips, "Add missing (3)" and Close both visible | 9 (before: 4606 px tall, buttons off screen) |
| `healthcare_standard_overview_w15_procurement_crews` | Crews left, procurement right without the inspector, 3D view in between | 5 |
| `healthcare_standard_overview_w15_report` | Weekly report (6 lines, truncated names) at the right end of the free area above the hint bar | 6 |
| `healthcare_standard_overview_w15_final` | Score panel centred and opaque, five metrics with weights, export / play again / menu buttons | 14 |
| `healthcare_standard_overview_w15_toast` | Event toast under the storey badge, title, text and OK button, 420 px | 6 |
| `healthcare_standard_zone-L00-Z3_w15` | Camera frames the zone (`frame_cells`) in the free area with the inspector pinned on the zone; ghost elements make the zone look washed out | New `frame_cells`; handed over: no highlight of the framed zone in the 3D view |
| `healthcare_standard_storey-1_w15` | Level 1 focus: tracker name in yellow, badge, blue rimmed plane at the floor of level 1, ground-floor elements ghost | 13 |
| `industrial_standard_installation-24_w20` | Close-up on the first pipe rack (4 cells, 52 elements) with the neighbouring modules and pumps | `frame_cells` through the kit instance; handed over: ghost fog |
| `industrial_standard_installation-31_w20` | Close-up on a tank (1 cell, 14 m): the translucent shell and the grid around it | as above |
| `industrial_standard_overview_w20_heat` | Heat overlay on: cells coloured by done share (orange, yellow, green) next to the grey not-started ones | Overlay by the element-visuals agent, reads well |

## Handed over

Fixed in the scene-level pass (WP-Q2, `bim_view.gd`, `site_builder.gd`, `zone_overlay.gd`, `main.gd` ground, `main-environment.tres`):

| Item | Status |
| --- | --- |
| Ghost fog | Fixed: not-started elements are a thin line-box outline (35 % alpha, discipline colour) with no filled volume; only the focused storey adds a fill of at most 5 %. The G toggle hides the outlines; in-progress parts are 90 % opaque and the opaque parts write depth (pre-pass) while the outlines are drawn after them. |
| Crane reach discs | Fixed: dashed amber ring at 40 % alpha plus an 6 % fill. |
| No highlight of the pinned / framed zone | Fixed: `ZoneOverlay.set_highlight_zone` draws a pulsing light rim along the zone boundary; `main.gd` calls it whenever `pinned_zone` changes (hovered zones get a steady rim). |
| Lime ground and flat sky | Fixed: sage terrain, a darker base plate under the site, exposure 0.72 and a gradient sky in the environment. |

Still open:

| Item | Image | For |
| --- | --- | --- |
| Kit close-ups need `ghost` kits to show an outline of the final shape at low alpha | `industrial_standard_installation-31_w20` | kits |
| The `Installations` panel (kits) was moved into the shared layout (third column left of the right dock); its look was not part of this set | none yet | kits: add `--panels=installations` to the harness when the panel stabilises |

## Next

* `--view=` bookmarks for pinned zones in the API (`view.frame`) could reuse `frame_cells`.
* On 1920x1080 and above the docks could grow (wider crew names); a width setting per dock is a small change in `main.gd`.
* Capture the menu scene and the report under the `hard` difficulty in the next pass.
