"""ZV-zlib container codec and packaging alias.

Delegates to nds_forge.adapters.container for codec implementation and secret provider interface.
"""
from __future__ import annotations

from ..adapters.container import (
    ContainerError,
    DictSecretProvider,
    EnvSecretProvider,
    EXPECTED_FORMATS,
    GENERATED_ZV_PATHS,
    REQUIRED_PACKAGE_PATHS,
    STATIC_ZV_PATHS,
    SecretProvider,
    SecretProviderError,
    pack_package_tree,
    pack_sqlite,
    unpack_sqlite,
)

__all__ = [
    "ContainerError",
    "DictSecretProvider",
    "EnvSecretProvider",
    "EXPECTED_FORMATS",
    "GENERATED_ZV_PATHS",
    "REQUIRED_PACKAGE_PATHS",
    "STATIC_ZV_PATHS",
    "SecretProvider",
    "SecretProviderError",
    "pack_package_tree",
    "pack_sqlite",
    "unpack_sqlite",
]
