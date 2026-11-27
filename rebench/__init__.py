"""RE Bench: a pre-Blender layout and rules bench for Rack Extension panels.

Phase 1 is the rules engine and a ``check`` command (``python -m rebench check
<project>``). It reads an RE project through RE-Blend's pure-Python project
layer and reports every finding with the *source* of the rule behind it, so a
spec requirement and an observed tool behaviour stay distinguishable. See
``docs/rebench-design.md``.

Pure Python, no ``bpy``.
"""

from .catalogue import RULES, Rule
from .check import BenchFinding, run_checks
from .profile import Profile

__all__ = ["RULES", "Rule", "BenchFinding", "run_checks", "Profile"]
