from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast


def inspect_public_api(site_packages: Path) -> int:
    sys.path.insert(0, str(site_packages))

    from ci_coordinator import config_control

    module_file = config_control.__file__
    if not isinstance(module_file, str):
        raise RuntimeError("public API import did not provide a file origin")
    module_path = Path(module_file).resolve()
    if not module_path.is_relative_to(site_packages):
        raise RuntimeError("public API import did not originate from the installed wheel")
    profile_path = (
        site_packages
        / "ci_coordinator"
        / "config_control"
        / "resources"
        / "policy-admission-result-profile.v1.json"
    )
    profile_value: object = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(profile_value, dict):
        raise TypeError("installed public API profile is not an object")
    projection = profile_value.get("pythonProjection")
    if not isinstance(projection, dict):
        raise TypeError("installed public API profile has no Python projection")
    symbols = projection.get("publicSymbols")
    if not isinstance(symbols, list) or not all(isinstance(symbol, str) for symbol in symbols):
        raise RuntimeError("installed public API symbol inventory is invalid")
    expected_symbols = cast(list[str], symbols)
    if config_control.__all__ != expected_symbols:
        raise RuntimeError("installed public API symbols differ from the result profile")
    if any(not hasattr(config_control, symbol) for symbol in expected_symbols):
        raise RuntimeError("installed public API is missing a declared symbol")

    result = config_control.admit_policy_document(b"{}", "toml")
    if not isinstance(result, tuple) or len(result) != 1:
        raise RuntimeError("installed admission operation returned an invalid failure algebra")
    if result[0].code != "source.unsupported_format":
        raise RuntimeError("installed admission operation returned an unexpected diagnostic")
    return len(expected_symbols)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: package_public_api_inspection.py SITE_PACKAGES", file=sys.stderr)
        return 2
    try:
        public_symbol_count = inspect_public_api(Path(sys.argv[1]).resolve())
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"publicSymbolCount": public_symbol_count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
