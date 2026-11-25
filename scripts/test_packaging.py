#!/usr/bin/env python3
"""TDD test suite for Melkyzedec/Toolbox packaging, manifests, README, and ZIP builder."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
PACKAGER = REPO_ROOT / "scripts" / "package-skills.py"
EXPECTED_SKILLS = [
    "github-open-source-release",
    "kanban-tasks",
    "mon-standard-skill",
    "qa-hostile-tests",
    "test-benchmark-audit",
]

ANTIGRAVITY_ALLOWED_KEYS = {
    "$schema",
    "name",
    "displayName",
    "description",
    "version",
    "disabled",
    "logo",
    "suggestedPrompts",
    "skills",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}

CLAUDE_ALLOWED_KEYS = {
    "$schema",
    "name",
    "displayName",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "metadata",
    "icon",
    "documentationUrl",
    "supportUrl",
    "privacyPolicyUrl",
    "termsOfServiceUrl",
    "defaultEnabled",
    "engines",
    "dependencies",
    "network",
    "userConfig",
    "types",
    "channels",
    "skills",
    "commands",
    "agents",
    "hooks",
    "mcpServers",
    "lspServers",
    "outputStyles",
    "workflows",
    "experimental",
}

MARKETPLACE_RESERVED_NAMES = {
    "claude-code-marketplace",
    "claude-code-plugins",
    "claude-plugins-official",
    "anthropic-marketplace",
    "anthropic-plugins",
    "agent-skills",
    "anthropic-agent-skills",
    "life-sciences",
    "knowledge-work-plugins",
    "claude-for-legal",
    "claude-for-financial-services",
    "financial-services-plugins",
    "first-party-plugins",
    "claude-tag-plugins",
    "claude-community",
    "claude-plugins-community",
    "healthcare",
    "anthropic-plugin-directory",
    "claude-plugin-directory",
    "inline",
    "builtin",
    "skills-dir",
    "synced",
    "claude-plugin-test",
}


def read_utf8_no_bom(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise AssertionError(f"UTF-8 BOM detected in {path}")
    return raw.decode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestManifests(unittest.TestCase):
    def test_antigravity_manifest(self) -> None:
        manifest_path = REPO_ROOT / ".antigravity-plugin" / "plugin.json"
        self.assertTrue(manifest_path.is_file(), f"Missing {manifest_path}")
        data = json.loads(read_utf8_no_bom(manifest_path))

        unknown = set(data.keys()) - ANTIGRAVITY_ALLOWED_KEYS
        self.assertEqual(unknown, set(), f"Unknown Antigravity keys: {unknown}")

        self.assertRegex(data["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertEqual(data["name"], "toolbox")
        self.assertEqual(data["displayName"], "Toolbox")
        self.assertEqual(data["version"], "1.0.0")
        self.assertTrue(isinstance(data.get("description"), str) and data["description"].strip())
        self.assertEqual(data["author"], {"name": "Melkyzedec", "url": "https://github.com/Melkyzedec"})
        self.assertEqual(data["license"], "MIT")
        self.assertEqual(data["repository"], "https://github.com/Melkyzedec/Toolbox")
        self.assertEqual(data["homepage"], "https://github.com/Melkyzedec/Toolbox")

        prompts = data.get("suggestedPrompts")
        self.assertIsInstance(prompts, list)
        self.assertGreaterEqual(len(prompts), 1)
        self.assertLessEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertIsInstance(prompt, str)
            self.assertTrue(prompt.strip())

    def test_claude_plugin_manifest(self) -> None:
        manifest_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
        self.assertTrue(manifest_path.is_file(), f"Missing {manifest_path}")
        data = json.loads(read_utf8_no_bom(manifest_path))

        unknown = set(data.keys()) - CLAUDE_ALLOWED_KEYS
        self.assertEqual(unknown, set(), f"Unknown Claude plugin keys: {unknown}")

        self.assertRegex(data["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertEqual(data["name"], "toolbox")
        self.assertEqual(data["displayName"], "Toolbox")
        self.assertEqual(data["version"], "1.0.0")
        self.assertTrue(isinstance(data.get("description"), str) and data["description"].strip())
        self.assertEqual(data["author"], {"name": "Melkyzedec", "url": "https://github.com/Melkyzedec"})
        self.assertEqual(data["homepage"], "https://github.com/Melkyzedec/Toolbox")
        self.assertEqual(data["repository"], "https://github.com/Melkyzedec/Toolbox")
        self.assertEqual(data["license"], "MIT")
        self.assertIsInstance(data.get("keywords"), list)
        self.assertGreaterEqual(len(data["keywords"]), 1)

    def test_claude_marketplace_manifest(self) -> None:
        mp_path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
        plugin_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
        self.assertTrue(mp_path.is_file(), f"Missing {mp_path}")
        mp = json.loads(read_utf8_no_bom(mp_path))
        plugin_data = json.loads(read_utf8_no_bom(plugin_path))

        self.assertEqual(mp["name"], "melkyzedec-toolbox")
        self.assertRegex(mp["name"], r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
        self.assertNotIn("..", mp["name"])
        self.assertNotIn(mp["name"], MARKETPLACE_RESERVED_NAMES)
        self.assertTrue(isinstance(mp.get("description"), str) and mp["description"].strip())
        self.assertIsInstance(mp.get("owner"), dict)
        self.assertEqual(mp["owner"].get("name"), "Melkyzedec")

        plugins = mp.get("plugins")
        self.assertIsInstance(plugins, list)
        self.assertGreaterEqual(len(plugins), 1)
        entry = plugins[0]
        self.assertEqual(entry["name"], plugin_data["name"])
        self.assertEqual(entry["source"], "./")
        self.assertTrue(entry["source"].startswith("./"))
        self.assertNotIn("..", entry["source"])
        self.assertNotIn("\\", entry["source"])
        self.assertNotIn("version", entry)
        self.assertNotIn("icon", entry)
        self.assertNotIn("documentationUrl", entry)


class TestReadme(unittest.TestCase):
    def test_readme_install_sections_and_safety(self) -> None:
        readme_path = REPO_ROOT / "README.md"
        text = read_utf8_no_bom(readme_path)

        self.assertIn("Tout installer en une commande", text)
        self.assertIn("Prompt", text)
        self.assertIn("agy plugin install https://github.com/Melkyzedec/Toolbox", text)
        self.assertIn("claude plugin install toolbox --marketplace Melkyzedec/Toolbox", text)
        for skill in EXPECTED_SKILLS:
            self.assertIn(skill, text)

        # Construct forbidden internal dir markers dynamically so self-scan stays clean.
        for forbidden_dir in ("." + "gemini/", "." + "codex/", "." + "memento/"):
            self.assertNotIn(forbidden_dir, text)
            self.assertNotIn(forbidden_dir.replace("/", "\\"), text)

        # Ensure no remote download-and-execute snippets.
        self.assertNotRegex(text, re.compile(r"(?i)\b(?:curl|wget)\b[^\r\n|]*\|\s*(?:ba)?sh\b"))
        self.assertNotRegex(text, re.compile(r"(?i)\b(?:Invoke-" + r"Expression|i" + r"ex)\b"))


class TestPackager(unittest.TestCase):
    def test_packager_generates_deterministic_posix_zips_and_sums(self) -> None:
        self.assertTrue(PACKAGER.is_file(), f"Missing {PACKAGER}")
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            out1 = Path(tmp1) / "out1"
            out2 = Path(tmp2) / "out2"

            res1 = subprocess.run(
                [sys.executable, "-B", str(PACKAGER), "--out", str(out1), "--json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res1.returncode, 0, f"stderr: {res1.stderr}\nstdout: {res1.stdout}")

            res2 = subprocess.run(
                [sys.executable, "-B", str(PACKAGER), "--out", str(out2), "--json"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res2.returncode, 0, f"stderr: {res2.stderr}\nstdout: {res2.stdout}")

            sums_path = out1 / "SHA256SUMS.txt"
            self.assertTrue(sums_path.is_file())
            sums_text = read_utf8_no_bom(sums_path)
            sum_lines = [line for line in sums_text.splitlines() if line.strip()]
            self.assertEqual(len(sum_lines), len(EXPECTED_SKILLS))

            recorded_sums = {}
            for line in sum_lines:
                digest, fname = line.split("  ", 1)
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                recorded_sums[fname] = digest

            for skill in EXPECTED_SKILLS:
                zip_name = f"{skill}-claude-desktop.zip"
                z1 = out1 / zip_name
                z2 = out2 / zip_name
                self.assertTrue(z1.is_file(), f"Missing {z1}")
                self.assertTrue(z2.is_file(), f"Missing {z2}")

                digest1 = sha256_bytes(z1.read_bytes())
                digest2 = sha256_bytes(z2.read_bytes())
                self.assertEqual(digest1, digest2, f"Non-deterministic ZIP for {skill}")
                self.assertEqual(recorded_sums.get(zip_name), digest1)

                skill_src = SKILLS_DIR / skill
                expected_rel = sorted(
                    p.relative_to(skill_src).as_posix()
                    for p in skill_src.rglob("*")
                    if p.is_file()
                    and "__pycache__" not in p.parts
                    and p.suffix not in {".pyc", ".pyo"}
                    and p.relative_to(skill_src).as_posix() != "agents/openai.yaml"
                )

                with zipfile.ZipFile(z1, "r") as zf:
                    names = zf.namelist()
                    self.assertTrue(names, f"Empty zip for {skill}")
                    for name in names:
                        self.assertNotIn("\\", name)
                        self.assertNotIn("..", name)
                        self.assertFalse(name.startswith("/"))
                        self.assertTrue(name.startswith(f"{skill}/"), f"Unexpected root entry: {name}")
                        self.assertNotIn("agents/openai.yaml", name)
                        self.assertNotIn("__pycache__", name)
                        self.assertFalse(name.endswith(".pyc"))
                    self.assertIn(f"{skill}/SKILL.md", names)
                    actual_rel = sorted(name[len(skill) + 1 :] for name in names if not name.endswith("/"))
                    self.assertEqual(actual_rel, expected_rel)

                    for rel in expected_rel:
                        self.assertEqual(
                            sha256_bytes(zf.read(f"{skill}/{rel}")),
                            sha256_bytes((skill_src / rel).read_bytes()),
                            f"Content mismatch for {skill}/{rel}",
                        )

    def test_packager_excludes_parasites_and_rejects_invalid_skills(self) -> None:
        self.assertTrue(PACKAGER.is_file(), f"Missing {PACKAGER}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_root = root / "skills"
            sample = skills_root / "sample-skill"
            (sample / "agents").mkdir(parents=True)
            (sample / "__pycache__").mkdir(parents=True)
            (sample / "SKILL.md").write_text(
                "---\nname: sample-skill\ndescription: Valid sample skill.\n---\n\nBody.\n",
                encoding="utf-8",
            )
            (sample / "agents" / "openai.yaml").write_text("interface:\n  display_name: Sample\n", encoding="utf-8")
            (sample / "__pycache__" / "mod.pyc").write_bytes(b"\x00\x01")
            (sample / "Thumbs.db").write_bytes(b"\x00")

            out_ok = root / "out_ok"
            res_ok = subprocess.run(
                [sys.executable, "-B", str(PACKAGER), "--skills-dir", str(skills_root), "--out", str(out_ok)],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
            with zipfile.ZipFile(out_ok / "sample-skill-claude-desktop.zip", "r") as zf:
                self.assertEqual(zf.namelist(), ["sample-skill/SKILL.md"])

            # Hostile case 1: missing SKILL.md
            broken = skills_root / "broken-skill"
            broken.mkdir()
            (broken / "notes.md").write_text("no skill manifest", encoding="utf-8")
            res_bad1 = subprocess.run(
                [sys.executable, "-B", str(PACKAGER), "--skills-dir", str(skills_root), "--out", str(root / "out_bad1")],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(res_bad1.returncode, 0)
            self.assertIn("SKILL.md", res_bad1.stderr + res_bad1.stdout)

            # Hostile case 2: UTF-8 BOM in SKILL.md
            (broken / "SKILL.md").write_bytes(
                b"\xef\xbb\xbf---\nname: broken-skill\ndescription: With BOM.\n---\n"
            )
            res_bad2 = subprocess.run(
                [sys.executable, "-B", str(PACKAGER), "--skills-dir", str(skills_root), "--out", str(root / "out_bad2")],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(res_bad2.returncode, 0)
            self.assertIn("BOM", res_bad2.stderr + res_bad2.stdout)


if __name__ == "__main__":
    unittest.main()
