"""Blender-side tests: run with the ``bpy`` module (``pip install bpy``, the
wheel matching your Python), skipped when it is not installed.

Each test starts from an empty factory scene with RE-Blend registered.
"""

import shutil
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _registered():
    import reblend

    reblend.register()
    yield
    reblend.unregister()


@pytest.fixture(autouse=True)
def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    yield bpy.context.scene


@pytest.fixture
def project(tmp_path) -> Path:
    """A writable copy of the SDK-convention fixture project."""
    root = tmp_path / "silence_detector"
    shutil.copytree(FIXTURES / "silence_detector", root)
    return root


@pytest.fixture
def imported(project, empty_scene):
    """The fixture project imported into the empty scene."""
    empty_scene.reblend.project_root = str(project) + "/"
    assert bpy.ops.reblend.import_project("EXEC_DEFAULT") == {"FINISHED"}
    return project
