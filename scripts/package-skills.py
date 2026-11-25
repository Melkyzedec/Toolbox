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
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return False


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

    frontmatter_pattern = re.compile(
        rf"^---\r?\n.*?^name:\s*{re.escape(skill_name)}\s*\r?\n.*?^description:",
        re.MULTILINE | re.DOTALL,
    )
    if not frontmatter_pattern.search(md_text):
        raise PackagingError(
            f"Invalid frontmatter in '{skill_name}/SKILL.md' (expected name: {skill_name} and description:)."
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

    skill_dirs = sorted([d for d in skills_dir.iterdir() if d.is_dir()], key=lambda p: p.name)
    if not skill_dirs:
        raise PackagingError("No skill directories found.")

    out_dir.mkdir(parents=True, exist_ok=True)

    validated: list[tuple[Path, list[Path]]] = []
    for skill_dir in skill_dirs:
        validated.append((skill_dir, validate_skill_dir(skill_dir)))

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
