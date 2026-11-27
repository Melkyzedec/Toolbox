#!/usr/bin/env python3
"""Build deterministic POSIX-compatible Claude Desktop ZIP archives for each skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
EXECUTABLE_SUFFIXES = {".py", ".ps1", ".sh"}
EXCLUDED_FILENAMES = {".DS_Store", "Thumbs.db"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_DIRS = {"__pycache__", ".git"}

# Private files block the build (same list as check-release.ps1 Assert-PublicPath). Never silently skipped.
PRIVATE_DIR_NAMES = {".memento", ".local", ".ssh", ".aws", ".gnupg"}
PRIVATE_NAME_RE = re.compile(
    r"(?i)^(?:"
    r"\.env(?:\.(?!example$).+)?"
    r"|\.gitleaksignore"
    r"|.*\.(?:sqlite3?|db|pem|pfx|key|p12|keystore|jks|kdbx|ovpn)(?:-(?:wal|shm|journal))?"
    r"|.*\.tfstate(?:\.backup)?|\.htpasswd"
    r"|id_(?:rsa|ed25519|ecdsa|dsa)"
    r"|credentials\.json|service-account[^/]*\.json|\.git-credentials|\.netrc|\.pgpass"
    r"|secrets\.(?:ya?ml|json|toml)"
    r")$"
)


PRIVATE_PATH_RE = re.compile(r"(?i)(?:^|/)(?:\.docker/config\.json|\.kube/config)$")


class PackagingError(Exception):
    """Controlled error raised when a skill directory fails validation."""


def is_excluded(rel_path: Path) -> bool:
    rel_posix = rel_path.as_posix()
    if rel_posix == "agents/openai.yaml":
        return True
    if any(part in EXCLUDED_DIRS for part in rel_path.parts):
        return True
    name = rel_path.name
    if name in EXCLUDED_FILENAMES or rel_path.suffix in EXCLUDED_SUFFIXES:
        return True
    return False


def is_private(rel_path: Path) -> bool:
    if any(part.lower() in PRIVATE_DIR_NAMES for part in rel_path.parts):
        return True
    if PRIVATE_PATH_RE.search(rel_path.as_posix()):
        return True
    return bool(PRIVATE_NAME_RE.match(rel_path.name))


def parse_frontmatter(md_text: str) -> dict[str, str] | None:
    """Return the top-level keys of the leading `---` ... `---` block, or None if there is no closed block."""
    lines = md_text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    keys: dict[str, str] = {}
    current: str | None = None
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if match:
            current = match.group(1)
            keys[current] = match.group(2).strip()
        elif current and line.startswith((" ", "\t")) and line.strip():
            keys[current] = (keys[current] + " " + line.strip()).strip()
    return keys


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1].strip()
    return value


def validate_skill_dir(skill_dir: Path) -> list[Path]:
    skill_name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise PackagingError(f"Missing required SKILL.md in skill '{skill_name}'.")

    raw_md = skill_md.read_bytes()
    if raw_md.startswith(b"\xef\xbb\xbf"):
        raise PackagingError(f"UTF-8 BOM detected in '{skill_name}/SKILL.md'.")
    try:
        md_text = raw_md.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackagingError(f"Invalid UTF-8 in '{skill_name}/SKILL.md': {exc}.") from exc

    meta = parse_frontmatter(md_text)
    description = _unquote(meta.get("description", "")) if meta else ""
    if description in (">", "|", ">-", "|-"):
        description = ""
    if (
        meta is None
        or _unquote(meta.get("name", "")) != skill_name
        or not description
    ):
        raise PackagingError(
            f"Invalid frontmatter in '{skill_name}/SKILL.md' (expected a leading --- block with "
            f"name: {skill_name} and a non-empty description:)."
        )

    files: list[Path] = []
    for candidate in sorted(skill_dir.rglob("*"), key=lambda p: p.relative_to(skill_dir).as_posix()):
        if candidate.is_symlink():
            raise PackagingError(f"Symlink not allowed in skill package: '{skill_name}/{candidate.relative_to(skill_dir).as_posix()}'.")
        if not candidate.is_file():
            continue
        rel = candidate.relative_to(skill_dir)
        if is_excluded(rel):
            continue
        if is_private(rel):
            raise PackagingError(f"Private file not allowed in skill package: '{skill_name}/{rel.as_posix()}'.")
        raw = candidate.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise PackagingError(f"UTF-8 BOM detected in '{skill_name}/{rel.as_posix()}'.")
        files.append(rel)

    if not files:
        raise PackagingError(f"No packagable files found in skill '{skill_name}'.")
    return files


def build_skill_zip(skill_dir: Path, files: list[Path], out_dir: Path) -> dict[str, object]:
    skill_name = skill_dir.name
    zip_name = f"{skill_name}-claude-desktop.zip"
    zip_path = out_dir / zip_name

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            arcname = f"{skill_name}/{rel.as_posix()}"
            info = zipfile.ZipInfo(filename=arcname, date_time=FIXED_ZIP_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o100755 if rel.suffix.lower() in EXECUTABLE_SUFFIXES else 0o100644
            info.external_attr = mode << 16
            zf.writestr(info, (skill_dir / rel).read_bytes())

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return {
        "skill": skill_name,
        "archive": zip_name,
        "path": str(zip_path),
        "files": len(files),
        "bytes": zip_path.stat().st_size,
        "sha256": digest,
    }


def package_all(skills_dir: Path, out_dir: Path) -> dict[str, object]:
    if not skills_dir.is_dir():
        raise PackagingError("Skills directory does not exist.")
    skills_root = skills_dir.resolve()
    out_resolved = out_dir.resolve()
    if out_resolved == skills_root or skills_root in out_resolved.parents:
        raise PackagingError("Output directory must not be inside the skills directory.")

    skill_dirs: list[Path] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if entry.is_symlink():
            raise PackagingError(f"Symlink not allowed as skill directory: '{entry.name}'.")
        if not entry.is_dir():
            continue
        if entry.resolve().parent != skills_root:
            raise PackagingError(f"Skill directory resolves outside the skills directory: '{entry.name}'.")
        skill_dirs.append(entry)
    if not skill_dirs:
        raise PackagingError("No skill directories found.")

    # Validate everything before writing anything: a failure leaves no partial archive behind.
    validated: list[tuple[Path, list[Path]]] = []
    for skill_dir in skill_dirs:
        validated.append((skill_dir, validate_skill_dir(skill_dir)))

    out_dir.mkdir(parents=True, exist_ok=True)

    packages = [build_skill_zip(skill_dir, files, out_dir) for skill_dir, files in validated]

    sums_lines = [f"{pkg['sha256']}  {pkg['archive']}\n" for pkg in packages]
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_path.write_bytes("".join(sums_lines).encode("utf-8"))

    return {
        "status": "ok",
        "count": len(packages),
        "outDir": str(out_dir),
        "checksumFile": str(sums_path),
        "packages": packages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package Toolbox skills as Claude Desktop ZIP archives.")
    parser.add_argument("--skills-dir", default="skills", help="Directory containing skill folders.")
    parser.add_argument("--out", required=True, help="Output directory for generated ZIPs and SHA256SUMS.txt.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary.")
    args = parser.parse_args(argv)

    try:
        result = package_all(Path(args.skills_dir), Path(args.out))
    except PackagingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for pkg in result["packages"]:
            print(f"{pkg['archive']}  ({pkg['files']} files, {pkg['bytes']} B)  sha256={pkg['sha256']}")
        print(f"Checksums written to {result['checksumFile']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
