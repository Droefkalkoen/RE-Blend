# Installing RE-Blend into Blender

RE-Blend is a **Blender 4.2 LTS+ extension** (`reblend/blender_manifest.toml`), not a legacy
add-on. Legacy add-ons were a single `.py` file with a `bl_info` dict, installed from the old
**Add-ons ▸ Install…** button. Extensions are a separate system: you never point Blender at the
`.toml`, and that old button will not find it. You install a packaged **`.zip`** (whose root
holds the manifest), or you drop the source folder into Blender's user-extensions directory.

Two things the manifest requires that trip people up:

1. **The manifest must sit next to `__init__.py`.** It does — both live in `reblend/`. When you
   build or symlink, the *extension root* is the `reblend/` folder, not the repo root.
2. **`lupa` must be importable inside Blender's Python.** `reblend/project/lua_reader.py` does a
   top-level `import lupa`, which `register()` pulls in, so without it enabling the add-on throws
   `ModuleNotFoundError: No module named 'lupa'`. The extension does not yet bundle the wheel
   (see *Bundling lupa* below), so for now install it into Blender's interpreter yourself.

**After a Blender upgrade** (Steam updates in place) the new version starts with a fresh
profile and its own Python: repeat sections 1 and 2 for the new version number. The junction
and `lupa` live per version, so 5.3 does not see what was set up for 5.2.

Substitute your own Blender path throughout — the examples use a Steam install of Blender 5.2 on
Windows (`E:\SteamLibrary\steamapps\common\Blender\5.2\`).

## 1. Put `lupa` in Blender's Python

Find Blender's interpreter — in Blender open **Scripting ▸ Python Console** and run:

```python
import sys; print(sys.executable)
```

Then, in a **terminal** (not the Blender console), install into *that* interpreter. PowerShell
needs the `&` call operator when the path is quoted:

```powershell
& "E:\SteamLibrary\steamapps\common\Blender\5.2\python\bin\python.exe" -m pip install lupa
```

cmd.exe (no `&`):

```cmd
"E:\SteamLibrary\steamapps\common\Blender\5.2\python\bin\python.exe" -m pip install lupa
```

If pip is missing, bootstrap it first with `-m ensurepip`. You can also do it from the Blender
Python console directly:

```python
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "lupa"])
```

Verify: `import lupa; print(lupa.__version__)` in the Blender console.

## 2a. Dev install — link the source folder (fastest to iterate)

Blender scans a per-version user-extensions directory. Point its `user_default` repo at your
working copy's `reblend/` folder, so edits show up on the next reload.

- Config root by OS:
  - Windows: `%APPDATA%\Blender Foundation\Blender\<ver>\`
  - Linux: `~/.config/blender/<ver>/`
  - macOS: `~/Library/Application Support/Blender/<ver>/`
- Target: `<config-root>\extensions\user_default\reblend`

On Windows, a directory **junction** works without admin rights or Developer Mode (adjust the
version and your clone path):

```cmd
mklink /J "%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\reblend" "C:\path\to\RE-Blend\reblend"
```

Linux/macOS symlink:

```sh
ln -s /path/to/RE-Blend/reblend "$HOME/.config/blender/5.2/extensions/user_default/reblend"
```

Because the manifest now lives inside `reblend/`, the linked folder already has everything
Blender needs — no separate copy step. Then in Blender: **Edit ▸ Preferences ▸ Get Extensions ▸**
the **⌄** dropdown (top-right) **▸ Refresh Local**, and enable **RE-Blend** in the **Add-ons**
list. The **RE-Blend** tab appears in the 3D-viewport N-panel.

(If junctions/symlinks are inconvenient, copying the `reblend/` folder to that target works too —
you just have to recopy after edits.)

## 2b. Build a distributable `.zip`

For a shippable artifact, build from the package dir and install the zip:

```sh
blender --command extension build --source-dir reblend --output-dir dist
```

Then **Get Extensions ▸ ⌄ ▸ Install from Disk…** and pick `dist/reblend-<version>.zip` (or drag
the zip onto the Blender window). A zip built this way still needs `lupa` present on the target
machine unless the wheel is bundled.

## Bundling `lupa` (for a self-contained zip)

To make the zip install without a manual `pip install`, vendor the platform wheel(s):

1. Download the matching `lupa` wheel(s) into `reblend/wheels/` (one per platform/Python you
   support — the wheel is a compiled extension, so it is platform-specific).
2. Uncomment and fill the `wheels = [...]` line in `reblend/blender_manifest.toml` (paths are
   relative to the manifest, e.g. `./wheels/lupa-2.8-cp311-...whl`).
3. Rebuild with the command in 2b.

Wheels are intentionally not committed yet; until they are, use the step-1 manual install.

## Troubleshooting

- **The Add-ons "Install…" button doesn't see the file** — expected; it only handles legacy
  add-ons. Use **Get Extensions ▸ Install from Disk** (zip) or the dev link in 2a.
- **`SyntaxError` when pip-installing** — you pasted a terminal command into the Blender *Python*
  console. Run pip in a terminal, or use the `subprocess.run(...)` form in step 1.
- **`ModuleNotFoundError: No module named 'lupa'` on enable** — step 1 didn't install into the
  interpreter Blender is actually using. Re-check `sys.executable` and install into that exact
  path.
- **Blender doesn't list the extension after linking** — confirm the linked folder is
  `…\extensions\user_default\reblend` and contains `blender_manifest.toml` beside `__init__.py`,
  then **Refresh Local**.
