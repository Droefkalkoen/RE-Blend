# Finding: where Reason draws the fold/unfold arrow

The SDK GUI design guidelines **require** that "the fold/unfold arrow in Reason must not overlap
with any widget", but neither they nor the designer manual say where the arrow is. This is the
measurement RE Bench's `fold-arrow` rule uses. It is an **observation of Reason**, not an SDK
statement, so re-measure if a Reason update moves the arrow.

**Method (2026-11-10).** Screenshots of the pilot (a 2U effect) in Reason, one per panel: front,
back, folded front, folded back. In all four the device spans exactly 754 screen pixels for its
3770 px panel, so 1 screen px = 5 hi-res px and every figure below is good to about ±5 px.
Coordinates are hi-res panel pixels from the panel's top-left corner.

| Panel | Arrow | Glyph box (x, y) |
| --- | --- | --- |
| Front (unfolded) | pointing down | x 80–125, y 60–90 |
| Back (unfolded) | pointing down | x 80–125, y 60–95 |
| Folded front | pointing right | x 90–120, y 55–100 |
| Folded back | pointing right | x 90–120, y 55–105 |

The arrow sits on the left rack ear, centred at about (100, 77) on every panel. On a folded
panel that is the 150 px strip's vertical midline.

**Rule.** RE Bench keeps (60, 35)–(145, 120) clear on every panel: the glyph plus about 20 px,
because the clickable area is not documented either. `rebench/profile.py` holds the numbers;
a project can override them in `rebench.profile.json`.

**Cross-check.** The pilot's earlier folded tape at (60, 40, 400 × 65) overlaps this box, which
is the overlap its readability/SDK audit fixed by hand. Its current layout (folded tape at x 430,
cable hole at x 260, front tape at y ≥ 146) clears it.
