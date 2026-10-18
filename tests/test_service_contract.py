from pathlib import Path

import yaml

from nishtyak_service.api import create_app
from nishtyak_service.config import Settings


def test_published_openapi_matches_implemented_api(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token="contract-test-token-" + "x" * 32)
    implemented = create_app(settings).openapi()
    contract = Path(__file__).parent / "contracts/openapi.yaml"
    assert yaml.safe_load(contract.read_text()) == implemented


def test_openapi_references_resolve(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token="contract-test-token-" + "x" * 32)
    schema = create_app(settings).openapi()

    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/")
                target = schema
                for segment in reference[2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
