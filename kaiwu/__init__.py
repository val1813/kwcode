try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("kwcode")
except Exception:
    __version__ = "1.6.2"
