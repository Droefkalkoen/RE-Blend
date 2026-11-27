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
   `ModuleNotFoundError: No module named 'lupa'`. The packaged zips (section 0) bundle it; a
   source folder linked into Blender (section 2a) does not, so install it there yourself
   (section 1).

## 0. Install a packaged zip (recommended)

`tools/package.py` builds one zip per platform with the `lupa` wheels for Blender's Pythons
(3.11 for Blender 4.2 to 4.5, 3.13 for 5.x) bundled, so nothing has to be installed into
Blender's own Python:

```sh
python3 tools/package.py                  # writes dist/reblend-<version>-<platform>.zip
python3 tools/package.py --blender blender   # also runs Blender's extension validator
```

It needs network access to PyPI the first time (wheels are cached in `build/wheels/`). Then in
Blender: **Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…** and pick the zip for
your platform (`windows-x64`, `macos-arm64`, `macos-x64` or `linux-x64`). Skip sections 1 and 2.

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

## 2b. Build a zip with Blender's own tool

`blender --command extension build --source-dir reblend --output-dir dist` also works, but it
does not bundle `lupa` (the committed manifest lists no wheels, so a linked source folder keeps
loading). Use `tools/package.py` (section 0) for a zip that installs on its own.

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
