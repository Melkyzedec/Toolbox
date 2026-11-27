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
        self.assertEqual(data["version"], "1.1.0")
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
        self.assertEqual(data["version"], "1.1.0")
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
        self.assertIn("v2.1.292+", text)
        self.assertIn("v2.1.275+", text)
        self.assertIn("claude plugin marketplace add Melkyzedec/Toolbox", text)
        self.assertIn("claude plugin install toolbox@melkyzedec-toolbox", text)
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


class TestPackagerHardening(unittest.TestCase):
    """Contre-audit 26/11/2026, M-03 : fichiers privés, liens symboliques, frontmatter et dossier de sortie."""

    VALID_MD = "---\nname: {name}\ndescription: CANARY_TEST_ skill de test.\n---\n\nCorps.\n"

    def _run(self, skills_root: Path, out_dir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(PACKAGER), "--skills-dir", str(skills_root), "--out", str(out_dir)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

    def _make_skill(self, root: Path, name: str = "sample-skill", md: str | None = None) -> Path:
        skill = root / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(md if md is not None else self.VALID_MD.format(name=name), encoding="utf-8")
        return skill

    def test_private_files_block_packaging(self) -> None:
        private_names = [
            ".env", ".env.local", ".env.production", "cle.pem", "prive.key", "base.sqlite", "base.sqlite3",
            "app.db", "base.sqlite-wal", "cert.pfx", "cert.p12", "store.jks", "app.keystore", "id_rsa",
            "id_ed25519", "credentials.json", "service-account-CANARY_TEST.json", "secrets.yaml",
            ".git-credentials", ".netrc", ".pgpass", ".ssh/config", ".aws/credentials", "references/.gnupg/x.txt",
            "coffre.kdbx", "client.ovpn", "terraform.tfstate", "infra/prod.tfstate.backup", ".htpasswd",
            ".kube/config", ".docker/config.json",
        ]
        for rel in private_names:
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                skill = self._make_skill(root)
                target = skill / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("CANARY_TEST_valeur_fictive\n", encoding="utf-8")
                out = root / "out"
                res = self._run(root / "skills", out)
                self.assertNotEqual(res.returncode, 0, f"{rel} empaqueté : {res.stdout}")
                self.assertIn("Private file", res.stderr)
                self.assertNotIn("CANARY_TEST_valeur_fictive", res.stderr + res.stdout)
                self.assertFalse(list(out.glob("*.zip")) if out.exists() else [], "aucune archive partielle")

    def test_env_example_and_public_lookalikes_still_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = self._make_skill(root)
            for rel in (".env.example", "references/credentials-guide.md", "references/keys.md", "id_rsa.pub"):
                (skill / rel).parent.mkdir(parents=True, exist_ok=True)
                (skill / rel).write_text("CANARY_TEST_\n", encoding="utf-8")
            res = self._run(root / "skills", root / "out")
            self.assertEqual(res.returncode, 0, res.stderr)
            with zipfile.ZipFile(root / "out" / "sample-skill-claude-desktop.zip") as zf:
                self.assertEqual(
                    sorted(zf.namelist()),
                    sorted(f"sample-skill/{n}" for n in (
                        ".env.example", "SKILL.md", "id_rsa.pub", "references/credentials-guide.md", "references/keys.md",
                    )),
                )

    def test_symlinked_skill_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside" / "linked"
            outside.mkdir(parents=True)
            (outside / "SKILL.md").write_text(self.VALID_MD.format(name="linked"), encoding="utf-8")
            (outside / "CANARY_TEST_hors_depot.txt").write_text("CANARY_TEST_\n", encoding="utf-8")
            (root / "skills").mkdir()
            try:
                (root / "skills" / "linked").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("liens symboliques indisponibles sur ce système")
            out = root / "out"
            res = self._run(root / "skills", out)
            self.assertNotEqual(res.returncode, 0, res.stdout)
            self.assertIn("Symlink", res.stderr)
            self.assertFalse(out.exists() and list(out.glob("*.zip")))

    def test_frontmatter_must_be_a_closed_leading_block(self) -> None:
        bad_docs = {
            "name hors frontmatter": "---\ntitle: x\n---\n\nname: sample-skill\ndescription: hors bloc\n",
            "bloc non fermé": "---\nname: sample-skill\ndescription: jamais fermé\n",
            "pas en tête": "Intro\n---\nname: sample-skill\ndescription: x\n---\n",
            "nom différent": "---\nname: autre-skill\ndescription: x\n---\n",
            "nom préfixé": "---\nname: sample-skill-evil\ndescription: x\n---\n",
            "description vide": "---\nname: sample-skill\ndescription:\n---\n",
            "description absente": "---\nname: sample-skill\n---\n",
        }
        for label, md in bad_docs.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._make_skill(root, md=md)
                res = self._run(root / "skills", root / "out")
                self.assertNotEqual(res.returncode, 0, f"{label} accepté")
                self.assertIn("frontmatter", res.stderr.lower())
        # Témoins : CRLF, description multi-ligne YAML (>) et guillemets acceptés
        for label, md in {
            "crlf": "---\r\nname: sample-skill\r\ndescription: ok\r\n---\r\n",
            "plié": "---\nname: sample-skill\ndescription: >\n  Texte plié\n  sur deux lignes.\n---\n",
            "guillemets": '---\nname: sample-skill\ndescription: "ok : avec deux-points"\n---\n',
        }.items():
            with self.subTest(temoin=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._make_skill(root, md=md)
                res = self._run(root / "skills", root / "out")
                self.assertEqual(res.returncode, 0, f"{label} : {res.stderr}")

    def test_out_dir_inside_skills_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = self._make_skill(root)
            for out in (skill / "dist", root / "skills", root / "skills" / "dist"):
                with self.subTest(out=str(out.relative_to(root))):
                    res = self._run(root / "skills", out)
                    self.assertNotEqual(res.returncode, 0, res.stdout)
                    self.assertIn("Output directory", res.stderr)
                    self.assertFalse((skill / "dist").exists())
                    self.assertFalse((root / "skills" / "dist").exists())
            # Témoin : un dossier de sortie voisin reste accepté, deux fois de suite à l'identique
            res1 = self._run(root / "skills", root / "dist")
            res2 = self._run(root / "skills", root / "dist")
            self.assertEqual(res1.returncode, 0, res1.stderr)
            self.assertEqual(res2.returncode, 0, res2.stderr)
            with zipfile.ZipFile(root / "dist" / "sample-skill-claude-desktop.zip") as zf:
                self.assertEqual(zf.namelist(), ["sample-skill/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
