"""Optional JSON Schema validation with an explicit unavailable result."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def validate_json_schema(instance: Any, schema_path: Path) -> bool:
    """Validate ``instance``; return False when the schema or validator is absent.

    Validation errors and malformed schema files propagate to the caller. A
    False result means no JSON Schema validation took place, not that the
    instance passed validation.
    """
    if not schema_path.is_file():
        return False
    try:
        import jsonschema
    except ImportError:
        return False
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=instance, schema=schema)
    return True
