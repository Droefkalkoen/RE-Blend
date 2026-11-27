"""Package RE-Blend as Blender extension zips, one per platform, with lupa bundled.

    python3 tools/package.py [--out dist] [--wheels-cache build/wheels] [--blender PATH]

Blender's extension system installs Python wheels listed in the manifest's
``wheels`` field, so the add-on works without touching Blender's own Python
(docs/install.md, section 1, becomes unnecessary). numpy ships with Blender;
lupa (MIT, the sandboxed Lua reader) is the only wheel.

Per platform the script copies ``reblend/`` to a staging folder, adds the lupa
wheels for Blender's Pythons (3.11 for Blender 4.2 to 4.5, 3.13 for 5.x),
writes ``platforms`` and ``wheels`` into the staged manifest and zips it. The
committed manifest stays wheel-free, so a source checkout linked into Blender
keeps working (it then needs lupa installed by hand, as before).

``--blender`` also runs ``blender --command extension validate`` on each zip.
Needs network access to PyPI for the first run; wheels are cached.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "reblend"
LUPA_VERSION = "2.8"
#: Blender's Python versions: 4.2 to 4.5 ship 3.11, 5.x ships 3.13.
PYTHONS = ("3.11", "3.13")
#: Blender platform name -> pip platform tag.
PLATFORMS = {
    "windows-x64": "win_amd64",
    "macos-arm64": "macosx_11_0_arm64",
    "macos-x64": "macosx_10_9_x86_64",
    "linux-x64": "manylinux2014_x86_64",
}
_SKIP = {"__pycache__", ".pytest_cache"}


def fetch_wheels(cache: Path) -> dict[str, list[Path]]:
    """Download (once) and return the lupa wheels per Blender platform."""
    cache.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[Path]] = {}
    for platform, tag in PLATFORMS.items():
        found: list[Path] = []
        for python in PYTHONS:
            before = set(cache.glob("*.whl"))
            subprocess.run([
                sys.executable, "-m", "pip", "download", f"lupa=={LUPA_VERSION}",
                "--only-binary=:all:", "--no-deps", "-q", "-d", str(cache),
                "--python-version", python, "--platform", tag,
            ], check=True)
            new = set(cache.glob("*.whl")) - before
            matches = new or {w for w in cache.glob(f"lupa-{LUPA_VERSION}-*.whl")
                              if _fits(w.name, python, tag)}
            found += sorted(matches - set(found))
        if not found:
            raise SystemExit(f"no lupa {LUPA_VERSION} wheel for {platform}")
        out[platform] = sorted(set(found))
    return out


def _fits(name: str, python: str, tag: str) -> bool:
    cp = "cp" + python.replace(".", "")
    arch = tag.split("_", 2)[-1] if tag.startswith("macosx") else tag.split("_", 1)[-1]
    platform_ok = tag.split("_")[0] in name and arch in name
    return platform_ok and (f"{cp}-{cp}" in name or "abi3" in name)


def staged_manifest(text: str, platform: str, wheels: list[Path]) -> str:
    listed = ",\n".join(f'  "./wheels/{w.name}"' for w in wheels)
    block = f'platforms = ["{platform}"]\nwheels = [\n{listed},\n]\n'
    text = re.sub(r"(?m)^# wheels = .*\n", "", text)
    return text.replace("\n[permissions]", f"\n{block}\n[permissions]", 1)


def package(out: Path, cache: Path, blender: str | None = None) -> list[Path]:
    manifest = (SOURCE / "blender_manifest.toml").read_text(encoding="utf-8")
    version = re.search(r'(?m)^version = "([^"]+)"', manifest).group(1)
    wheels = fetch_wheels(cache)
    out.mkdir(parents=True, exist_ok=True)
    zips = []
    for platform, files in wheels.items():
        target = out / f"reblend-{version}-{platform}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(SOURCE.rglob("*")):
                rel = path.relative_to(SOURCE)
                if path.is_dir() or _SKIP & set(rel.parts) or path.suffix == ".pyc":
                    continue
                if rel.name == "blender_manifest.toml":
                    z.writestr(str(rel), staged_manifest(manifest, platform, files))
                else:
                    z.write(path, str(rel))
            for wheel in files:
                z.write(wheel, f"wheels/{wheel.name}")
        zips.append(target)
        if blender:
            subprocess.run([blender, "--command", "extension", "validate", str(target)],
                           check=True)
    return zips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(REPO / "dist"))
    parser.add_argument("--wheels-cache", default=str(REPO / "build" / "wheels"))
    parser.add_argument("--blender", help="Blender binary, to validate each zip")
    args = parser.parse_args(argv)
    for z in package(Path(args.out), Path(args.wheels_cache), args.blender):
        print(z)
    return 0


if __name__ == "__main__":
    sys.exit(main())
