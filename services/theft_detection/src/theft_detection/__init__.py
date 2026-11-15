"""SafeSC object-removal event detection: a standalone, CPU-first video analysis module.

Public surface::

    from theft_detection import TheftDetector, Config, load_config, Event
"""
__version__ = "0.1.0"

import os as _os

# Numpy's BLAS would otherwise start one thread per logical core and reserve a work buffer for
# each (measured: 494 MB vs 140 MB committed per process on a 12-thread CPU). This module only
# multiplies tiny matrices, and the detector has its own explicit thread cap, so one BLAS
# thread is enough. setdefault: an operator's own setting wins. Takes effect only when this
# package is imported before numpy, which is the case for the CLI and the API server.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

from .config import Config, load_config  # noqa: E402
from .events.schema import SCHEMA_VERSION, Event  # noqa: E402
from .pipeline import FrameResult, TheftDetector  # noqa: E402

__all__ = ["TheftDetector", "Config", "load_config", "Event", "FrameResult", "SCHEMA_VERSION", "__version__"]
