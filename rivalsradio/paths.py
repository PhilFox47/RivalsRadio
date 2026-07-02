"""Filesystem layout: user data directory and bundled (shipped) assets."""

from __future__ import annotations

import os
import sys

ART_KINDS = ("logo", "portrait", "signature", "background")


def data_dir() -> str:
    """Per-user data directory (config, art, caches). Created on demand."""
    base = os.environ.get("RIVALSRADIO_HOME") or os.path.join(
        os.path.expanduser("~"), ".rivalsradio")
    os.makedirs(base, exist_ok=True)
    return base


def art_dir(kind: str) -> str:
    d = os.path.join(data_dir(), "art", kind + "s")
    os.makedirs(d, exist_ok=True)
    return d


def art_cache_dir() -> str:
    d = os.path.join(data_dir(), "artcache")
    os.makedirs(d, exist_ok=True)
    return d


def bundled(*parts: str) -> str:
    """Path of a file shipped with the app (source tree or PyInstaller bundle)."""
    root = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, *parts)
