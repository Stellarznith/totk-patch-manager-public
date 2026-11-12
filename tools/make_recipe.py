"""Recreate the v1.2.8 runtime recipe from reviewed text and pinned upstream."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RECIPE_ID = "67a2bbc520378201d5bd5bcfcec07dcc917c9b86261a2692f9464479b479ff8c"


def make_recipe(upstream: Path, output: Path) -> str:
    manifest = (ROOT / "src/recipe/manifest.json").read_bytes()
    definition = json.loads(manifest)
    version = (upstream / "VERSION").read_text(encoding="utf-8").strip()
    if version.split("+", 1)[0] != definition["basis"] or not (upstream / "runtime").is_dir():
        raise ValueError(f"Use upstream {definition['basis']} at {definition['source_commit']}")

    members = {"manifest.json": manifest}
    for name, entry in definition["files"].items():
        original = upstream / "runtime" / name
        base = original.read_bytes() if original.is_file() else b""
        members[entry["base"]] = base.replace(b"\r\n", b"\n")
        members[entry["modified"]] = (ROOT / "src/runtime-overlay" / name).read_bytes()

    fingerprint = hashlib.sha256()
    for name, data in sorted(members.items()):
        fingerprint.update(name.encode("utf-8"))
        fingerprint.update(data)
    actual = fingerprint.hexdigest()
    if actual != RECIPE_ID:
        raise ValueError(
            f"Recipe fingerprint {actual} differs from {RECIPE_ID}. "
            "Check the pinned upstream and overlay bytes."
        )

    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"recipe_id={make_recipe(args.upstream, args.output)}")


if __name__ == "__main__":
    main()
