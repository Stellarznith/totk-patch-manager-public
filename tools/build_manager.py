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
import workspace

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
                        help="Optional private, unpacked payload directory (containing manager-version.txt, assets.zip and prebuilt/)")
    parser.add_argument("--portable-online", action="store_true",
                        help="Mark output as a standalone portable online build (includes Python runtime and multi-version recipes without private prebuilt binaries)")
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
        version = (payload / "manager-version.txt").read_bytes().strip() if (payload / "manager-version.txt").is_file() else b""
        if (not version
                or not (payload / "assets.zip").is_file()
                or not (payload / "prebuilt/manifest.json").is_file()):
            parser.error("--local-payload requires an unpacked payload with manager-version.txt, assets.zip and prebuilt/manifest.json")
        if output.is_relative_to(payload.resolve()):
            parser.error("Keep build output outside the private payload directory")

    output.mkdir(parents=True)
    # The payload is copied wholesale; refuse before writing a single byte.
    needed = sum(p.stat().st_size for p in payload.iterdir() if p.is_file()) if payload else 0
    needed += 512 * 1024**2
    workspace.require(output, needed, "构建输出目录")
    recipe_digest = make_recipe(args.upstream, output / 'recipe.zip')
    print(f"recipe_id={recipe_digest}", flush=True)
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

    # Bundle verified legacy recipe baselines alongside the primary recipe.zip so
    # offline rebuilds of 0.4.0 and 0.3.13 games keep working without network access.
    import hashlib, zipfile
    with zipfile.ZipFile(output / "recipe.zip") as primary_rz:
        current_basis = str(json.loads(primary_rz.read("manifest.json")).get("basis", ""))
    legacy_candidates = []
    if payload:
        legacy_candidates.extend(sorted(payload.glob("recipe-*.zip")))
        if (payload / "recipe.zip").is_file():
            legacy_candidates.append(payload / "recipe.zip")
    for extra in (
        ROOT / "dist/release-v1.3.5-update/recipe-0.4.1.zip",
        ROOT / "dist/release-v1.3.4-update/recipe-0.4.0.zip",
        ROOT / "build/app-v1.3.4-final/recipe.zip",
        ROOT / "build/app-1.2.9/recipe-0.3.13.zip",
    ):
        if extra.is_file():
            legacy_candidates.append(extra)
    seen_bases = {current_basis}
    for candidate in legacy_candidates:
        if not candidate.is_file() or not zipfile.is_zipfile(candidate):
            continue
        try:
            with zipfile.ZipFile(candidate) as cz:
                c_basis = str(json.loads(cz.read("manifest.json")).get("basis", ""))
        except (KeyError, ValueError, zipfile.BadZipFile):
            continue
        if c_basis and c_basis not in seen_bases:
            shutil.copyfile(candidate, output / f"recipe-{c_basis}.zip")
            seen_bases.add(c_basis)

    if args.portable_online and not payload:
        (output / "portable-online.txt").write_text("online\n", encoding="ascii")
        for notice in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            if (ROOT / notice).is_file():
                shutil.copyfile(ROOT / notice, output / notice)

    if payload and (payload / "assets.zip").is_file():
        shutil.copyfile(payload / "assets.zip", output / "assets.zip")
    if payload:
        shutil.copytree(payload / "prebuilt", output / "prebuilt")
        manifest_path = output / "prebuilt/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        sys.path.insert(0, str(MANAGER))
        import core as manager_core
        paired = manager_core._VERIFIED_LEGACY_PREBUILT_PAIRS.get(manifest_sha)
        legacy_ok = (
            paired is not None
            and manifest.get("recipe_id") == paired[0]
            and recipe_digest in paired[1]
        )
        if manifest.get("recipe_id") != recipe_digest and not legacy_ok:
            raise ValueError(
                f"Prebuilt manifest recipe_id {manifest.get('recipe_id')} does not match "
                f"generated recipe.zip {recipe_digest}; rebuild prebuilt with the current recipe."
            )
        if manifest.get("assets_id"):
            h = hashlib.sha256()
            with zipfile.ZipFile(output / "assets.zip") as az:
                for name in sorted(az.namelist()):
                    h.update(name.encode())
                    h.update(az.read(name))
            actual_assets = h.hexdigest()
            if manifest["assets_id"] != actual_assets:
                raise ValueError(
                    f"Prebuilt manifest assets_id {manifest['assets_id']} does not match "
                    f"assets.zip {actual_assets}."
                )

    references = ("System.Windows.Forms", "System.Drawing", "System.Web.Extensions", "System.Management")
    subprocess.run(
        [str(compiler.resolve()), "/nologo", "/warnaserror", "/codepage:65001",
         "/target:winexe", "/platform:x64", f"/out:{output / EXE}",
         f"/win32manifest:{MANAGER / 'Manager.manifest'}",
         *(f"/r:{name}.dll" for name in references),
         *(str(path) for path in sorted(MANAGER.glob("Manager*.cs")))],
        check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    prebuilt_check = "; assert core.bundled_components()['recipe_id']" if payload else ""
    subprocess.run(
        [str(runtime / "python.exe"), "-X", "utf8", "-c",
         "from compression import zstd; import manager_install; import core; assert core.mod_backend().Center; assert core.ps5_action_backend().repair" + prebuilt_check + "; print('backend imports OK')"],
        cwd=output, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print(output / EXE)


if __name__ == "__main__":
    main()
