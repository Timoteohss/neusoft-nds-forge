"""Project config JSON Schema integration and optional dependency behavior."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nds_forge.config import ConfigError, load_config

CONFIG = '''\
schema_version = 1
project_id = "schema-fixture"
workspace = "./work"

[region]
id = "r1"
source = "bbox"
bbox = [0, 0, 1, 1]

[source]
kind = "pbf"
path = "fixture.pbf"

[pipeline]
target = "source.verify"
'''


class ProjectSchemaValidationTests(unittest.TestCase):
    def test_valid_project_config_reports_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.toml"
            path.write_text(CONFIG, encoding="utf-8")
            config = load_config(path)
        self.assertIs(config.schema_validated, True)

    def test_invalid_project_config_fails_json_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.toml"
            path.write_text(CONFIG.replace('target = "source.verify"', "target = 42"), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Project schema validation failed"):
                load_config(path)

    def test_missing_jsonschema_is_explicitly_unvalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.toml"
            path.write_text(CONFIG, encoding="utf-8")
            with patch.dict(sys.modules, {"jsonschema": None}):
                config = load_config(path)
        self.assertIs(config.schema_validated, False)


if __name__ == "__main__":
    unittest.main()
