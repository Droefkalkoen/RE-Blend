"""Render layer: batch render, strip stitching, output validation (§5).

:mod:`stitcher`, :mod:`validators` and :mod:`compositor` are pure numpy and
:mod:`shadows` is plain Python, so all four are testable without Blender;
:mod:`bpy_io` and :mod:`renderer` drive a live Blender scene and import
``bpy`` lazily.
"""
