"""Grava em openapi.yaml o contrato completo, com as rotas internas."""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api.app import create_app  # noqa: E402
from src.api.openapi import build_openapi  # noqa: E402
from src.core.config import Settings  # noqa: E402


def main() -> None:
    app = create_app(Settings(google_api_key=None))
    contract = yaml.safe_dump(
        build_openapi(app, include_internal=True),
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    )
    contract = re.sub(
        r"^(  version: .+)$",
        r"\1 # x-release-please-version",
        contract,
        count=1,
        flags=re.MULTILINE,
    )
    (ROOT / "openapi.yaml").write_text(contract, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
