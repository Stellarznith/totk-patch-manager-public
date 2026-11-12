"""Check the Git index before publishing a source-only release."""

import re
import subprocess
import sys

TEXT_EXTENSIONS = {".c", ".cpp", ".h", ".cs", ".py", ".md", ".txt", ".json",
                   ".manifest", ".config"}
ROOT_FILES = {"LICENSE", "README.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
              ".gitattributes", ".gitignore"}
SCREENSHOT = "docs/images/manager-controller.png"
LOCAL_PATH = re.compile(r"(?i)[a-z]:[\\/]+(?:users|codex|totk-windows|ns2|rg-romfs)[\\/]+")
SECRET = re.compile(
    r"(?i)-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
    r"|(?:gh[pousr]_[0-9a-z]{36}|github_pat_[0-9a-z_]{50,})"
    r"|^\s*(?:titlekey|master_key_\w+|header_key)\s*=\s*[0-9a-f]{32,}",
    re.MULTILINE,
)


def main() -> int:
    from pathlib import PurePosixPath

    records = subprocess.check_output(["git", "ls-files", "--stage", "-z"]).split(b"\0")
    problems = []
    checked = 0
    for record in filter(None, records):
        metadata, filename = record.split(b"\t", 1)
        mode, blob, stage = metadata.decode("ascii").split()
        name = filename.decode("utf-8")
        path = PurePosixPath(name)
        if mode not in {"100644", "100755"} or stage != "0":
            problems.append(f"{name}: unexpected index entry")
            continue
        if (name not in ROOT_FILES and name != SCREENSHOT
                and (path.parts[0] not in {"docs", "src", "tests", "tools", "licenses"}
                     or path.suffix not in TEXT_EXTENSIONS)):
            problems.append(f"{name}: outside the release scope")
            continue
        data = subprocess.check_output(["git", "cat-file", "blob", blob])
        if len(data) > 1024 * 1024:
            problems.append(f"{name}: over 1 MiB")
        elif name == SCREENSHOT:
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                problems.append(f"{name}: expected the reviewed PNG")
        else:
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{name}: expected UTF-8 text")
                continue
            if "\x00" in content or LOCAL_PATH.search(content) or SECRET.search(content):
                problems.append(f"{name}: binary data, private path or possible secret")
        checked += 1
    if not checked:
        problems.append("The Git index is empty.")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"Source release index check passed ({checked} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
