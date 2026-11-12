"""Build the WinForms manager in a new directory using locally supplied tools."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

# The embedded Python _pth can omit the script's directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_recipe import ROOT, make_recipe

MANAGER = ROOT / "src/manager"
EXE = "王国之泪补丁管理器.exe"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--python-kit", type=Path, required=True,
                        help="A local Windows x64 Python 3.14 embedded directory")
    parser.add_argument("--output", type=Path, required=True, help="New build output directory")
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--local-payload", type=Path,
                        help="Optional private, unpacked v1.2.8 payload directory")
    args = parser.parse_args()

    compiler = args.compiler
    if compiler is None:
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not compiler.is_file():
        parser.error("Supply a .NET Framework x64 csc.exe with --compiler")

    python_kit = args.python_kit.resolve()
    for name in ("python.exe", "python314.zip", "python314._pth"):
        if not (python_kit / name).is_file():
            parser.error(f"--python-kit must contain {name}")

    output = args.output.resolve()
    if output.is_relative_to(python_kit) or output.is_relative_to(args.upstream.resolve()):
        parser.error("Keep build output outside the input directories")

    payload = args.local_payload
    if payload:
        if (not (payload / "manager-version.txt").is_file()
                or (payload / "manager-version.txt").read_bytes().strip() != b"1.2.8"
                or not (payload / "assets.zip").is_file()
                or not (payload / "prebuilt/manifest.json").is_file()):
            parser.error("--local-payload requires the unpacked v1.2.8 version, assets and manifest")
        if output.is_relative_to(payload.resolve()):
            parser.error("Keep build output outside the private payload directory")

    output.mkdir(parents=True)
    print(f"recipe_id={make_recipe(args.upstream, output / 'recipe.zip')}", flush=True)
    for name in ("manager_install.py", "manager_update.py", "manager-version.txt", EXE + ".config"):
        shutil.copyfile(MANAGER / name, output / name)
    core = (MANAGER / "core.py").read_text(encoding="utf-8")
    marker = "_MOD_CENTER_SOURCE = None"
    if core.count(marker) != 1:
        raise ValueError("Manager module bundle marker is missing or duplicated")
    mods = (MANAGER / "manager_mods.py").read_text(encoding="utf-8")
    hashes = {}
    for expected in sorted((args.upstream / "tools/data").glob("expected-v*.json")):
        digest = json.loads(expected.read_text(encoding="utf-8"))["exefs_sha256"].get("main")
        if digest:
            hashes[digest] = expected.stem.removeprefix("expected-v")
    core = core.replace(marker, "_MOD_CENTER_SOURCE = " + repr(mods), 1)
    core = core.replace("_MOD_GAME_HASHES = {}", "_MOD_GAME_HASHES = " + repr(hashes), 1)
    marker = "_PS5_ACTION_SOURCE = None"
    if core.count(marker) != 1:
        raise ValueError("PS5 action-prompt bundle marker is missing or duplicated")
    prompt = (MANAGER / "manager_ps5_prompt.py").read_text(encoding="utf-8")
    core = core.replace(marker, "_PS5_ACTION_SOURCE = " + repr(prompt), 1)
    (output / "core.py").write_text(core, encoding="utf-8", newline="\n")

    runtime = output / "python"
    shutil.copytree(python_kit, runtime)
    pth = runtime / "python314._pth"
    lines = pth.read_text(encoding="utf-8-sig").splitlines()
    if ".." not in lines:
        pth.write_text("\n".join([*lines, ".."]) + "\n", encoding="utf-8")

    for name in ("assets.zip", "recipe-0.3.13.zip"):
        if payload and (payload / name).is_file():
            shutil.copyfile(payload / name, output / name)
    if payload:
        shutil.copytree(payload / "prebuilt", output / "prebuilt")

    references = ("System.Windows.Forms", "System.Drawing", "System.Web.Extensions", "System.Management")
    subprocess.run(
        [str(compiler.resolve()), "/nologo", "/warnaserror", "/codepage:65001",
         "/target:winexe", "/platform:x64", f"/out:{output / EXE}",
         f"/win32manifest:{MANAGER / 'Manager.manifest'}",
         *(f"/r:{name}.dll" for name in references),
         *(str(path) for path in sorted(MANAGER.glob("Manager*.cs")))],
        check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    subprocess.run(
        [str(runtime / "python.exe"), "-X", "utf8", "-c",
         "from compression import zstd; import manager_install; import core; assert core.mod_backend().Center; assert core.ps5_action_backend().repair; print('backend imports OK')"],
        cwd=output, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print(output / EXE)


if __name__ == "__main__":
    main()
