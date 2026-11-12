"""Package the manager files allowed in the GitHub self-update asset."""

import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/manager"))
import manager_update


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    version = (args.app / manager_update.VERSION).read_text(encoding="ascii").strip()
    args.output.mkdir(parents=True)
    archive = args.output / manager_update.asset_name(version)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in manager_update.FILES:
            z.write(args.app / name, name)
        for name in manager_update.NOTICES:
            z.write(ROOT / name, name)
    manager_update.payload(archive, version)
    if archive.stat().st_size > manager_update.MAX_BYTES:
        raise ValueError("Manager update ZIP exceeds the download size limit")
    checksum = manager_update.manager_install.sha(archive)
    (args.output / "SHA256SUMS.txt").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    print(f"{checksum}  {archive}")


if __name__ == "__main__":
    main()
