"""ZV-zlib container codec and packaging alias.

Delegates to nds_forge.adapters.container for codec implementation and secret provider interface.
"""
from __future__ import annotations

from ..adapters.container import (
    ContainerError,
    DictSecretProvider,
    EnvSecretProvider,
    SecretProvider,
    SecretProviderError,
    expected_formats,
    generated_paths,
    pack_package_tree,
    pack_sqlite,
    package_paths,
    static_paths,
    unpack_sqlite,
)

__all__ = [
    "ContainerError",
    "DictSecretProvider",
    "EnvSecretProvider",
    "SecretProvider",
    "SecretProviderError",
    "expected_formats",
    "generated_paths",
    "pack_package_tree",
    "pack_sqlite",
    "package_paths",
    "static_paths",
    "unpack_sqlite",
]
