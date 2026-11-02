"""Write the OpenAPI schema without a server or the keychain (spec §17): `python -m researchly.openapi <out.json>`."""

import json
import sys
from pathlib import Path

from researchly.app import create_app


def main(argv: list[str] | None = None) -> None:
    out = Path((sys.argv[1:] if argv is None else argv)[0])
    schema = create_app(token="openapi-dump").openapi()  # noqa: S106 (placeholder; the schema holds no token)
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", "utf-8", newline="\n")


if __name__ == "__main__":
    main()
