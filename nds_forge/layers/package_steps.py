"""Profile-pinned package assembly stage registration."""
from __future__ import annotations

from typing import Any

from ..registry import EXPERIMENTAL, Step, StepContext, replace_step
from .package_assemble import assemble_package


def _execute(context: StepContext) -> dict[str, Any]:
    return assemble_package(context)


def register_package_step() -> None:
    replace_step(Step(
        "package.assemble", EXPERIMENTAL,
        ("geometry.bmd", "names.compile", "address.compile", "poi.compile", "routing.main",
         "routing.aux", "catalogs.build", "bootstrap.verify"),
        "package", "Assemble the package tree for the declared product/update-region identity",
        inputs=("geometry.bmd", "names.compile", "address.compile", "poi.compile", "routing.main",
                "routing.aux", "catalogs.build/catalogs.json", "bootstrap.verify/bootstrap-report.json",
                "neusoft.bootstrap_dir", "neusoft.bootstrap_profile_id", "neusoft.region_binding", "neusoft.container_keys", "neusoft.names", "neusoft.poi"),
        outputs=("db/", "package-layout.json"),
        capabilities=("declared_region_package_tree",),
        external_inputs=("neusoft.bootstrap_dir",),
        cacheable=False,
    ), _execute)
