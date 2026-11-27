"""Run RE-Blend's headless CLI from Blender's command line, however it is installed.

    blender -b MyDevice.blend --python <path to reblend>/run_cli.py -- render --dirty

As a Blender extension the package's module is ``bl_ext.<repository>.reblend``
(already imported when the extension is enabled), so ``import reblend`` would
fail; this script finds the loaded module by its file, or imports the folder
it sits in.
"""

import importlib
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent


def _package():
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and Path(path).resolve() == _PKG / "__init__.py":
            return module
    sys.path.insert(0, str(_PKG.parent))
    return importlib.import_module(_PKG.name)


if __name__ == "__main__":
    _package().cli()
