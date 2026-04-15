"""skilltool — PyPI-like client for skill.md packages."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("skilltool")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
