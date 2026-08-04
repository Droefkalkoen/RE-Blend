# Findings: driver values and Blender's stale path error

Observed behaviour of Blender 4.2 LTS, not a documented contract. Recorded because it
cost a debugging session and looks exactly like a bug in RE-Blend when it isn't one.

## A driver value is a custom property, not a driver

`Add State Action → Custom Property` (`DRIVER_VALUE`, `state_tables.driver_value`) creates a
plain float ID property named by the designer, on the object named in the action's Target. It
is not a named driver, and Blender keeps no registry of such names — property names are not
unique across objects, so a bare `wasbeerkat` addresses nothing.

A driver reads it as a **Single Property** variable in two halves:

| Field | Value | Language |
| --- | --- | --- |
| Prop (ID Type + ID) | `Object` / the owner object | datablock pointer |
| Path | `["wasbeerkat"]` | RNA path, relative to that ID |
| Expression | `var` | Python, driver namespace |

Only the Expression field is Python, which is why `frame` works bare there (Blender pre-loads
it, and there is exactly one current frame per evaluation) while `["wasbeerkat"]` cannot work
bare anywhere: it is a subscript fragment, meaningless without the thing it subscripts.

The owner is *usually* the element's registration empty — that is what `_default_value_owner`
seeds the dialog with — but the field is editable and falls back to the active object when the
element has no resolvable `re_registration`. **The action's Target is the only authority.**
`Copy Property Path` reports it; don't assume the empty.

## The stale invalid flag

A driver variable that evaluates **once** against a property that does not exist gets
`DTAR_FLAG_INVALID` stamped on its `DriverTarget`. The UI draws the Path field red from that
flag rather than re-resolving on redraw, and only a *successful evaluation* clears it.

What does **not** clear it:

- the property later appearing (e.g. Generate Rig creating it)
- the popover's **Update Dependencies** button
- retargeting the variable's path at a name that does exist

So a driver wired before the property existed — or one first pointed at a different, misspelled
or since-renamed value — keeps showing `ERROR: Invalid Python expression` and a red path long
after the wiring became correct. **Cure: step the scene frame once.** That forces the
re-evaluation that clears the flag.

RE-Blend's mitigation is to make the window unreachable: `rigs.ensure_value_property` creates
the property when the action is *declared*, not at Generate Rig time, so the name always exists
before anything can point at it. `Copy Property Path` still warns and names the cure, for tables
authored before that change.

## Why not `bpy.app.driver_namespace`

A helper registered there would let an expression read a value by bare name
(`re_val("wasbeerkat")`), which is what designers expect on first contact. RE-Blend deliberately
does not ship one:

1. **No dependency is created.** Blender builds driver update relations from the *declared
   variables*; a function reaching into `bpy.data` has no traceable inputs, so the depsgraph
   does not know to re-evaluate. It usually looks right — drivers re-evaluate on frame change —
   and then serves a stale value. Worse than the flag above, because stepping a frame is what
   masks it rather than what fixes it.
2. **It does not survive a file load.** The namespace is runtime-only and needs a `@persistent`
   `load_post` handler to re-register.
3. It exceeds the fast simple-expression evaluator, so it requires Auto-Run Python Scripts —
   false on a machine that merely opens the `.blend`.

The verbose two-part address is the cost of a link the depsgraph actually tracks.

## Removal leaves the property behind, on purpose

`Remove State Action` edits `re_states` only. The custom property and its f-curves stay on the
object: deleting the property would break any driver already pointing at it, silently and at a
distance. The f-curve is genuine cruft, though — `apply_state_table` prunes only channels the
*current* table touches — so the operator reports what it left for hand-cleanup.
