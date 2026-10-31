"""ytkit — a terminal toolkit for YouTube media, captions and metadata."""

from importlib.metadata import PackageNotFoundError, version

# pyproject.toml is the one place the version is set (`uv version --bump ...`).
try:
    __version__ = version("ytkit-tui")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
