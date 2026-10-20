"""Profile-pinned package assembly stage registration."""
from __future__ import annotations

from typing import Any

from ..registry import EXPERIMENTAL, Step, StepContext, replace_step
from .package_assemble import assemble_v9


def _execute(context: StepContext) -> dict[str, Any]:
    return assemble_v9(context)


def register_package_step() -> None:
    replace_step(Step(
        "package.assemble", EXPERIMENTAL,
        ("geometry.bmd", "names.compile", "poi.compile", "routing.main", "routing.aux",
         "catalogs.build", "bootstrap.verify"),
        "package", "Assemble the explicit 21-path Curitiba v9 package tree",
        inputs=("geometry.bmd", "names.compile", "poi.compile", "routing.main", "routing.aux",
                "catalogs.build/catalogs.json", "bootstrap.verify/bootstrap-report.json",
                "neusoft.bootstrap_dir", "neusoft.container_keys", "neusoft.names", "neusoft.poi"),
        outputs=("db/", "package-layout.json"),
        capabilities=("curitiba_v9_21_path_package_tree",),
        external_inputs=("neusoft.bootstrap_dir",),
        cacheable=False,
    ), _execute)
