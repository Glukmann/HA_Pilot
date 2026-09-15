"""Test the Pilot manifest."""

import json
from pathlib import Path

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "pilot"


def test_manifest_valid():
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
    assert manifest["domain"] == "pilot"
    assert manifest["name"] == "Pilot Eyes"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert manifest["integration_type"] == "hub"
    assert manifest["codeowners"] == ["@Glukmann"]
    assert manifest["requirements"] == []
    assert manifest["version"]
    assert manifest["issue_tracker"].startswith("https://")
    assert manifest["documentation"].startswith("https://")
