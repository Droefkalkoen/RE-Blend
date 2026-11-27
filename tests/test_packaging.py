"""tools/package.py and the extension manifest Blender validates."""

import importlib.util
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "reblend" / "blender_manifest.toml"


def _package_module():
    spec = importlib.util.spec_from_file_location("package", REPO / "tools" / "package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_permission_texts_fit_blenders_limits():
    """Blender refuses a reason over 64 characters or ending in a full stop."""
    data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    for key, reason in data["permissions"].items():
        assert len(reason) <= 64, key
        assert not reason.endswith("."), key


def test_staged_manifest_lists_platform_and_wheels():
    package = _package_module()
    wheel = Path("lupa-2.8-cp311-cp311-win_amd64.whl")
    staged = tomllib.loads(package.staged_manifest(
        MANIFEST.read_text(encoding="utf-8"), "windows-x64", [wheel]))
    assert staged["platforms"] == ["windows-x64"]
    assert staged["wheels"] == [f"./wheels/{wheel.name}"]
    assert staged["permissions"]  # still a table after the insert


def test_wheel_matching():
    package = _package_module()
    assert package._fits("lupa-2.8-cp311-cp311-win_amd64.whl", "3.11", "win_amd64")
    assert not package._fits("lupa-2.8-cp313-cp313-win_amd64.whl", "3.11", "win_amd64")
    assert package._fits("lupa-2.8-cp39-abi3-macosx_10_9_x86_64.whl", "3.13", "macosx_10_9_x86_64")
