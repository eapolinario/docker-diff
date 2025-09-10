"""
Docker Image Comparison Database Manager
Provides functions to store and query Docker image file comparison results in SQLite
"""

from .cli import DockerImageDB, main
try:
    from ._version import version as __version__
except Exception:  # pragma: no cover - fallback for editable/no-scm
    __version__ = "0.0.0"
