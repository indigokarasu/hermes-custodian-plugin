"""Directory-plugin entry point for Custodian.

Hermes loads a directory plugin by executing this file with ``__path__`` set to
the plugin directory, so ``register(ctx)`` must be reachable from here. The
implementation lives in the ``hermes_custodian_plugin`` package; the pyproject
entry-point group covers pip installs, and this re-export covers directory
installs (including catalog installs, which check out the repo as a directory).
"""

import os
import sys

try:  # directory-plugin load: this file is a package (__path__ = plugin dir)
    from .hermes_custodian_plugin import register
except ImportError:  # imported as a standalone top-level module (tooling, tests)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hermes_custodian_plugin import register

__all__ = ["register"]
