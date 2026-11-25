#!/usr/bin/env python3
"""Batterie de tests hostiles (0 USD, dossier temporaire isolé) pour kanban.py."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import kanban  # noqa: E402


KANBAN_PY = str(SCRIPT_DIR / "kanban.py")


def run_cli(board_dir: Path, args: list[str], stdin_text: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", KANBAN_PY, "--dir", str(board_dir), "--json", *args],
        input=stdin_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        check=False,
    )


class TestKanbanTasks(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="canary_kanban_")
        self.board_dir = Path(self._tmp.name) / "kanban"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_01_init_layout_utf8_lf_no_bom(self) -> None:
        res = run_cli(self.board_dir, ["init", "--title", "CANARY_TEST_Tableau Éditorial"])
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["action"], "init")

        for rel in ("kanban.json", "KANBAN.md", "kanban.html", ".gitignore"):
            p = self.board_dir / rel
            self.assertTrue(p.exists(), f"{rel} manquant")
            raw = p.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM détecté dans {rel}")
            self.assertNotIn(b"\r\n", raw, f"CRLF détecté dans {rel}")

        self.assertTrue((self.board_dir / "tasks").is_dir())
        self.assertTrue((self.board_dir / "archive").is_dir())
        md = (self.board_dir / "KANBAN.md").read_text(encoding="utf-8")
        self.assertIn("kanban-plugin: board", md)
        self.assertIn("## Idées", md)
        self.assertIn("## Terminé", md)

    def test_02_crud_subtasks_block_filters_and_body_preservation(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        add_res = run_cli(
            self.board_dir,
            [
                "add",
                "--title", "CANARY_TEST_Rédiger procédure",
                "--status", "a_faire",
                "--priority", "haute",
                "--tags", "doc,qa",
                "--category", "Ops",
                "--assignee", "CANARY_TEST_user@example.test",
                "--subtask", "Étape Alpha",
                "--subtask", "Étape Bêta",
                "--body", "Contexte important à préserver intact.",
            ],
        )
        self.assertEqual(add_res.returncode, 0, add_res.stderr)
        tid = json.loads(add_res.stdout)["id"]
        self.assertEqual(tid, "T-001")

        # Check by index & by substring, preserve prose
        self.assertEqual(run_cli(self.board_dir, ["check", tid, "1"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["check", tid, "Bêta"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["uncheck", tid, "2"]).returncode, 0)

        self.assertEqual(run_cli(self.board_dir, ["block", tid, "--reason", "Attente validation"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["edit", tid, "--append-note", "Note ajoutée"]).returncode, 0)

        show_res = run_cli(self.board_dir, ["show", tid])
        self.assertEqual(show_res.returncode, 0)
        card = json.loads(show_res.stdout)
        self.assertEqual(card["subtasks_done"], 1)
        self.assertEqual(card["subtasks_total"], 2)
        self.assertTrue(card["blocked"])
        self.assertIn("Contexte important à préserver intact.", card["body"])
        self.assertIn("Note ajoutée", card["body"])

        # Filter blocked + tag
        list_res = run_cli(self.board_dir, ["list", "--blocked", "--tag", "qa"])
        self.assertEqual(json.loads(list_res.stdout)["count"], 1)

        self.assertEqual(run_cli(self.board_dir, ["unblock", tid]).returncode, 0)
        list_blocked = run_cli(self.board_dir, ["list", "--blocked"])
        self.assertEqual(json.loads(list_blocked.stdout)["count"], 0)

    def test_03_agent_vs_human_validation_gate(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Mission IA", "--status", "en_cours"]).returncode, 0)

        # Agent cannot move directly to termine
        bad = run_cli(self.board_dir, ["move", "T-001", "termine", "--actor", "agent:worker", "--as-agent"])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("en_revue", json.loads(bad.stdout)["error"])

        # Actor prefix agent: also blocked without --as-agent
        bad2 = run_cli(self.board_dir, ["move", "T-001", "termine", "--actor", "agent:assistant"])
        self.assertNotEqual(bad2.returncode, 0)

        # Agent moves to en_revue cleanly
        ok_rev = run_cli(self.board_dir, ["move", "T-001", "en_revue", "--actor", "agent:worker", "--as-agent"])
        self.assertEqual(ok_rev.returncode, 0, ok_rev.stderr)

        # Human validates to termine
        ok_done = run_cli(self.board_dir, ["move", "T-001", "termine", "--actor", "humain"])
        self.assertEqual(ok_done.returncode, 0, ok_done.stderr)

    def test_04_claim_contention_and_parallel_multiprocess_add(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)

        # Spawn 6 concurrent processes adding tasks simultaneously
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        procs = [
            subprocess.Popen(
                [sys.executable, "-B", KANBAN_PY, "--dir", str(self.board_dir), "--json",
                 "add", "--title", f"CANARY_TEST_Parallele_{i}", "--status", "a_faire"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )
            for i in range(6)
        ]
        created_ids = set()
        for proc in procs:
            out, err = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, f"Échec process parallèle : {err} / {out}")
            created_ids.add(json.loads(out)["id"])
        self.assertEqual(created_ids, {"T-001", "T-002", "T-003", "T-004", "T-005", "T-006"})

        # Atomic claim contention
        c1 = run_cli(self.board_dir, ["claim", "T-001", "--by", "agent:alpha", "--start"])
        self.assertEqual(c1.returncode, 0, c1.stderr)
        self.assertEqual(json.loads(c1.stdout)["moved_to"], "en_cours")

        c2 = run_cli(self.board_dir, ["claim", "T-001", "--by", "agent:beta"])
        self.assertNotEqual(c2.returncode, 0)
        self.assertIn("déjà prise", json.loads(c2.stdout)["error"])

        self.assertEqual(run_cli(self.board_dir, ["release", "T-001"]).returncode, 0)
        c3 = run_cli(self.board_dir, ["claim", "T-001", "--by", "agent:beta"])
        self.assertEqual(c3.returncode, 0)

    def test_05_dependencies_implicit_block_and_cycle_detection(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Base", "--status", "a_faire"]).returncode, 0)
        self.assertEqual(
            run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Suite", "--status", "a_faire", "--depends-on", "T-001"]).returncode,
            0,
        )

        card2 = json.loads(run_cli(self.board_dir, ["show", "T-002"]).stdout)
        self.assertTrue(card2["implicitly_blocked"])
        self.assertEqual(card2["unmet_dependencies"], ["T-001"])

        # Cycle T-001 -> T-002 -> T-001 must be rejected
        cyc = run_cli(self.board_dir, ["edit", "T-001", "--depends-on", "T-002"])
        self.assertNotEqual(cyc.returncode, 0)
        self.assertIn("Cycle", json.loads(cyc.stdout)["error"])

        # Completing T-001 unblocks T-002 implicitly
        self.assertEqual(run_cli(self.board_dir, ["move", "T-001", "termine"]).returncode, 0)
        card2_after = json.loads(run_cli(self.board_dir, ["show", "T-002"]).stdout)
        self.assertFalse(card2_after["implicitly_blocked"])

        # Inject a manual cycle in disk YAML -> validate must catch it
        p1 = next((self.board_dir / "tasks").glob("T-001-*.md"))
        meta1, body1 = kanban.parse_task_markdown(p1.read_text(encoding="utf-8"))
        meta1["depends_on"] = ["T-002"]
        kanban.write_task_record(p1, meta1, body1)
        val = run_cli(self.board_dir, ["validate"])
        self.assertNotEqual(val.returncode, 0)
        self.assertTrue(any("Cycle" in e for e in json.loads(val.stdout)["errors"]))

    def test_06_temporal_horizons_defer_work_item_age_cycle_and_rework(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        # Task 1: active in_progress, will age + get edited without resetting Work Item Age
        self.assertEqual(
            run_cli(
                self.board_dir,
                [
                    "add",
                    "--title", "CANARY_TEST_Stagnante",
                    "--status", "en_cours",
                    "--horizon", "cette_semaine",
                    "--start-date", "2026-11-01",
                    "--due-date", "2026-11-15",
                ],
            ).returncode,
            0,
        )
        # Task 2: deferred until 2026-12-10 -> hidden from focus on 2026-11-24
        self.assertEqual(
            run_cli(
                self.board_dir,
                [
                    "add",
                    "--title", "CANARY_TEST_En_Veille",
                    "--status", "a_faire",
                    "--defer-until", "2026-12-10",
                ],
            ).returncode,
            0,
        )
        # Task 3: completed with rework en_revue -> en_cours -> en_revue -> termine
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Rework", "--status", "a_faire"]).returncode, 0)

        p1 = next((self.board_dir / "tasks").glob("T-001-*.md"))
        m1, b1 = kanban.parse_task_markdown(p1.read_text(encoding="utf-8"))
        m1["created_at"] = "2026-11-01T10:00:00Z"
        m1["updated_at"] = "2026-11-01T10:00:00Z"
        m1["history"] = ["2026-11-01T10:00:00Z | creation -> en_cours | humain"]
        kanban.write_task_record(p1, m1, b1)

        # Minor edit updates updated_at to 2026-11-24, MUST NOT reset age_in_status_days!
        self.assertEqual(run_cli(self.board_dir, ["edit", "T-001", "--append-note", "Petite retouche"]).returncode, 0)

        p3 = next((self.board_dir / "tasks").glob("T-003-*.md"))
        m3, b3 = kanban.parse_task_markdown(p3.read_text(encoding="utf-8"))
        m3["created_at"] = "2026-11-10T08:00:00Z"
        m3["updated_at"] = "2026-11-12T18:00:00Z"
        m3["history"] = [
            "2026-11-10T08:00:00Z | creation -> a_faire | humain",
            "2026-11-10T10:00:00Z | a_faire -> en_cours | humain",
            "2026-11-11T10:00:00Z | en_cours -> en_revue | agent:qa",
            "2026-11-11T12:00:00Z | en_revue -> en_cours | humain",
            "2026-11-12T18:00:00Z | en_cours -> termine | humain",
        ]
        kanban.write_task_record(p3, m3, b3)

        focus = json.loads(run_cli(self.board_dir, ["focus", "--today", "2026-11-24"]).stdout)
        self.assertEqual(focus["deferred_hidden"], 1)
        all_focus_ids = {
            item["id"]
            for sec in ("to_review", "overdue_or_soon", "in_progress", "this_week_ready", "next_ready_todo")
            for item in focus[sec]
        }
        self.assertIn("T-001", all_focus_ids)
        self.assertNotIn("T-002", all_focus_ids)

        triage = json.loads(run_cli(self.board_dir, ["triage", "--today", "2026-11-24"]).stdout)
        self.assertEqual([t["id"] for t in triage["stale"]], ["T-001"])
        self.assertGreaterEqual(triage["stale"][0]["age_in_status_days"], 20.0)
        self.assertEqual([t["id"] for t in triage["overdue"]], ["T-001"])

        stats = json.loads(run_cli(self.board_dir, ["stats", "--today", "2026-11-24"]).stdout)
        self.assertEqual(stats["rework_total"], 1)
        self.assertEqual(stats["avg_cycle_time_hours"], 56.0)

    def test_07_archive_never_reuses_ids_and_satisfies_dependencies(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Done", "--status", "termine"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Wait", "--status", "a_faire", "--depends-on", "T-001"]).returncode, 0)

        arch = run_cli(self.board_dir, ["archive", "--all-done"])
        self.assertEqual(arch.returncode, 0, arch.stderr)
        self.assertEqual(json.loads(arch.stdout)["archived_ids"], ["T-001"])
        self.assertFalse(list((self.board_dir / "tasks").glob("T-001-*.md")))
        self.assertTrue(list((self.board_dir / "archive").glob("T-001-*.md")))

        # Next created task must be T-003 (never reuse T-001), even if next_id was manually lowered
        cfg = kanban.load_config(self.board_dir)
        cfg["next_id"] = 1
        kanban.save_config(self.board_dir, cfg)
        add3 = json.loads(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Trois"]).stdout)
        self.assertEqual(add3["id"], "T-003")

        # T-002 dependency on archived T-001 remains satisfied
        card2 = json.loads(run_cli(self.board_dir, ["show", "T-002"]).stdout)
        self.assertFalse(card2["implicitly_blocked"])

    def test_08_obsidian_sha256_guard_and_sync_md(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Obsidian", "--status", "idees"]).returncode, 0)

        md_path = self.board_dir / "KANBAN.md"
        original_md = md_path.read_text(encoding="utf-8")
        lines = original_md.split("\n")
        card_line = next(l for l in lines if "T-001" in l)
        lines.remove(card_line)
        target_idx = lines.index("## En cours") + 1
        lines.insert(target_idx, card_line)
        md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

        # render without --force-md must refuse overwriting manual changes
        blocked_render = run_cli(self.board_dir, ["render"])
        self.assertNotEqual(blocked_render.returncode, 0)
        self.assertIn("sync-md", json.loads(blocked_render.stdout)["error"])

        # sync-md imports the move to en_cours and refreshes SHA-256
        synced = run_cli(self.board_dir, ["sync-md", "--actor", "humain"])
        self.assertEqual(synced.returncode, 0, synced.stderr)
        sync_data = json.loads(synced.stdout)
        self.assertEqual(sync_data["changes"], [{"id": "T-001", "from": "idees", "to": "en_cours"}])

        card = json.loads(run_cli(self.board_dir, ["show", "T-001"]).stdout)
        self.assertEqual(card["status"], "en_cours")
        self.assertEqual(run_cli(self.board_dir, ["render"]).returncode, 0)

    def test_09_batch_stdin_with_unicode_accents(self) -> None:
        batch_ops = [
            {"op": "init", "title": "CANARY_TEST_Lot Accent"},
            {"op": "add", "title": "CANARY_TEST_Évaluer l'idée nº 1", "status": "a_faire", "subtasks": ["Vérifier UTF-8", "Valider rendu"]},
            {"op": "move", "id": "T-001", "status": "en_cours", "actor": "humain"},
            {"op": "check", "id": "T-001", "target": "1"},
        ]
        res = run_cli(self.board_dir, ["batch", "--stdin"], stdin_text=json.dumps(batch_ops, ensure_ascii=False))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["count"], 4)
        card = json.loads(run_cli(self.board_dir, ["show", "T-001"]).stdout)
        self.assertEqual(card["title"], "CANARY_TEST_Évaluer l'idée nº 1")
        self.assertEqual(card["status"], "en_cours")
        self.assertEqual(card["subtasks_done"], 1)

    def test_10_hostile_windows_filenames_and_yaml_special_chars(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        for reserved in ("CON", "NUL", "aux"):
            slug = kanban.slugify(reserved)
            self.assertNotIn(slug, kanban.WINDOWS_RESERVED_STEMS)

        tricky_title = 'CANARY_TEST_Fix #42: "alerte" \'test\' [WIP] & contrôle'
        res = run_cli(self.board_dir, ["add", "--title", tricky_title, "--status", "a_faire"])
        self.assertEqual(res.returncode, 0, res.stderr)
        card = json.loads(run_cli(self.board_dir, ["show", "T-001"]).stdout)
        self.assertEqual(card["title"], tricky_title)
        self.assertEqual(json.loads(run_cli(self.board_dir, ["validate"]).stdout)["valid"], True)

    def test_11_hostile_html_xss_and_zero_unsafe_dom(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        xss = 'CANARY_TEST_</script><img src=x onerror=alert(1)>'
        res = run_cli(self.board_dir, ["add", "--title", xss, "--body", xss])
        self.assertEqual(res.returncode, 0, res.stderr)

        html_text = (self.board_dir / "kanban.html").read_text(encoding="utf-8")
        self.assertNotIn("\r", html_text)
        self.assertIn("split(/\\r?\\n/)", html_text)
        self.assertNotIn("</script><img", html_text)
        self.assertIn("<\\/script><img", html_text)
        self.assertNotIn("innerHTML", html_text)
        self.assertNotIn("eval(", html_text)
        self.assertNotIn("https://", html_text)
        self.assertNotIn("http://", html_text)

    def test_12_validate_catches_corrupted_yaml_dates_dups_and_missing_deps(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Valide"]).returncode, 0)

        # Duplicate ID + bad date order + unknown status + missing dep
        dup_file = self.board_dir / "tasks" / "T-099-bad.md"
        dup_file.write_text(
            "---\n"
            "id: T-001\n"
            "title: \"CANARY_TEST_Bad\"\n"
            "status: colonne_fantome\n"
            "priority: normale\n"
            "start_date: \"2026-12-10\"\n"
            "due_date: \"2026-12-01\"\n"
            "depends_on:\n"
            "  - T-999\n"
            "created_at: \"2026-11-24T10:00:00Z\"\n"
            "updated_at: \"2026-11-24T10:00:00Z\"\n"
            "---\n",
            encoding="utf-8",
            newline="\n",
        )
        # Corrupted YAML
        broken_file = self.board_dir / "tasks" / "T-002-broken.md"
        broken_file.write_text("pas de frontmatter ici\n", encoding="utf-8", newline="\n")

        val = run_cli(self.board_dir, ["validate"])
        self.assertEqual(val.returncode, 1)
        report = json.loads(val.stdout)
        self.assertFalse(report["valid"])
        joined = "\n".join(report["errors"])
        self.assertIn("ID en double", joined)
        self.assertIn("colonne inconnue", joined)
        self.assertIn("start_date", joined)
        self.assertIn("dépendance introuvable", joined)
        self.assertIn("frontmatter YAML manquant", joined)

    def test_13_serve_interactive_api_and_csrf(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init", "--title", "CANARY_TEST_Serve"]).returncode, 0)
        static_html = (self.board_dir / "kanban.html").read_text(encoding="utf-8")
        self.assertIn('"interactive": false', static_html)
        self.assertIn('"csrf_token": ""', static_html)

        httpd, token = kanban.create_serve_httpd(self.board_dir, port=0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/kanban.html", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                live_html = resp.read().decode("utf-8")
            self.assertIn('"interactive": true', live_html)
            self.assertIn(token, live_html)

            # POST without X-Kanban-Token must be rejected (403)
            bad_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/action",
                data=json.dumps({"op": "add", "title": "CANARY_TEST_Refuse"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req, timeout=5)
            self.assertEqual(ctx.exception.code, 403)
            ctx.exception.close()

            def post_action(payload: dict) -> dict:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/action",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Kanban-Token": token},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    return json.loads(resp.read().decode("utf-8"))

            added = post_action({
                "op": "add",
                "title": "CANARY_TEST_Live_Card",
                "status": "a_faire",
                "subtasks": ["Sous-tâche 1"],
            })
            self.assertTrue(added["ok"])
            tid = added["result"]["id"]

            moved = post_action({"op": "move", "id": tid, "status": "en_cours", "actor": "humain"})
            self.assertEqual(moved["result"]["to"], "en_cours")

            checked = post_action({"op": "check", "id": tid, "target": "1"})
            self.assertTrue(checked["result"]["done"])

            saved = post_action({
                "op": "save_card",
                "id": tid,
                "title": "CANARY_TEST_Live_Updated",
                "status": "en_revue",
                "priority": "haute",
                "blocked": True,
                "blocked_reason": "Attente retour",
                "body": "Contenu modifié depuis l'interface.\n\n- [x] Sous-tâche 1",
            })
            self.assertEqual(saved["result"]["status"], "en_revue")

            card = json.loads(run_cli(self.board_dir, ["show", tid]).stdout)
            self.assertEqual(card["title"], "CANARY_TEST_Live_Updated")
            self.assertEqual(card["status"], "en_revue")
            self.assertEqual(card["priority"], "haute")
            self.assertTrue(card["blocked"])
            self.assertEqual(card["blocked_reason"], "Attente retour")
            self.assertEqual(card["subtasks_done"], 1)

            # Static HTML written on disk must stay non-interactive without token
            disk_html = (self.board_dir / "kanban.html").read_text(encoding="utf-8")
            self.assertIn('"interactive": false', disk_html)
            self.assertNotIn(token, disk_html)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_14_custom_labels_and_bulk_rename_categories_tags(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init", "--title", "CANARY_TEST_Taxonomie"]).returncode, 0)
        self.assertEqual(
            run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_A", "--category", "Produit", "--tags", "qa,doc"]).returncode,
            0,
        )
        self.assertEqual(
            run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_B", "--category", "Produit", "--tags", "qa,test"]).returncode,
            0,
        )

        cfg_res = run_cli(
            self.board_dir,
            [
                "config",
                "--category-label", "Domaine",
                "--tag-label", "Étiquette",
                "--rename-column", "idees=Idées_brutes",
                "--rename-horizon", "cette_semaine=Prioritaire",
            ],
        )
        self.assertEqual(cfg_res.returncode, 0, cfg_res.stderr)
        cfg = kanban.load_config(self.board_dir)
        self.assertEqual(cfg["labels"], {"category": "Domaine", "tag": "Étiquette"})
        self.assertIn("Idées_brutes", (self.board_dir / "KANBAN.md").read_text(encoding="utf-8"))

        # CLI bulk rename category & tag (merge qa -> test without duplicate tag!)
        rcat = run_cli(self.board_dir, ["rename-category", "Produit", "Projets"])
        self.assertEqual(rcat.returncode, 0, rcat.stderr)
        self.assertEqual(json.loads(rcat.stdout)["updated_cards"], 2)

        rtag = run_cli(self.board_dir, ["rename-tag", "qa", "test"])
        self.assertEqual(rtag.returncode, 0, rtag.stderr)
        self.assertEqual(json.loads(rtag.stdout)["updated_cards"], 2)

        card_b = json.loads(run_cli(self.board_dir, ["show", "T-002"]).stdout)
        self.assertEqual(card_b["category"], "Projets")
        self.assertEqual(card_b["tags"], ["test"])

        # Remove tag via empty new name
        rm_tag = run_cli(self.board_dir, ["rename-tag", "doc", ""])
        self.assertEqual(rm_tag.returncode, 0)
        card_a = json.loads(run_cli(self.board_dir, ["show", "T-001"]).stdout)
        self.assertEqual(card_a["tags"], ["test"])

        # Serve API: save_settings, add_taxonomy, rename_category, rename_tag
        httpd, token = kanban.create_serve_httpd(self.board_dir, port=0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            def post_action(payload: dict) -> dict:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/action",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Kanban-Token": token},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    return json.loads(resp.read().decode("utf-8"))

            s_resp = post_action({
                "op": "save_settings",
                "title": "CANARY_TEST_Perso",
                "category_label": "Pôle",
                "tag_label": "Label",
                "columns": [{"id": "idees", "label": "Boîte à idées", "wip_limit": None}],
                "horizons": [{"id": "cette_semaine", "label": "Maintenant"}],
            })
            self.assertEqual(s_resp["state"]["labels"], {"category": "Pôle", "tag": "Label"})

            t_resp = post_action({"op": "add_taxonomy", "kind": "category", "value": "Maison"})
            self.assertIn("Maison", t_resp["state"]["categories"])

            r_resp = post_action({"op": "rename_category", "old": "Projets", "new": "Clients"})
            self.assertEqual(r_resp["result"]["updated_cards"], 2)
            self.assertIn("Clients", r_resp["state"]["categories"])
            self.assertNotIn("Projets", r_resp["state"]["categories"])

            rt_resp = post_action({"op": "rename_tag", "old": "test", "new": "verif"})
            self.assertEqual(rt_resp["result"]["updated_cards"], 2)
            self.assertIn("verif", rt_resp["state"]["tags"])
            md = (self.board_dir / "KANBAN.md").read_text(encoding="utf-8")
            self.assertIn("## Boîte à idées", md)
            self.assertIn("[Clients]", md)
            self.assertIn("#verif", md)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_15_delete_guard_bidirectional_deps_and_unknown_status_fallback(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init", "--title", "CANARY_TEST_Delete"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Base", "--status", "en_cours"]).returncode, 0)
        self.assertEqual(
            run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_Dep", "--status", "a_faire", "--depends-on", "T-001"]).returncode,
            0,
        )

        # Bidirectional dependency link & elapsed hours on T-001
        card1 = json.loads(run_cli(self.board_dir, ["show", "T-001"]).stdout)
        self.assertEqual(card1["unblocks"], ["T-002"])
        self.assertIn("age_in_status_hours", card1)

        # Delete T-001 without --force must fail cleanly listing T-002
        bad_del = run_cli(self.board_dir, ["delete", "T-001"])
        self.assertNotEqual(bad_del.returncode, 0)
        err_msg = json.loads(bad_del.stdout)["error"]
        self.assertIn("T-002", err_msg)

        # Delete T-001 with --force removes T-001 and strips T-001 from T-002 depends_on
        ok_del = run_cli(self.board_dir, ["delete", "T-001", "--force"])
        self.assertEqual(ok_del.returncode, 0, ok_del.stderr)
        self.assertEqual(json.loads(ok_del.stdout)["removed_from_dependents"], ["T-002"])
        card2 = json.loads(run_cli(self.board_dir, ["show", "T-002"]).stdout)
        self.assertEqual(card2["depends_on"], [])
        self.assertFalse(card2["implicitly_blocked"])

        # Unknown status in hand-edited file still renders in kanban.html fallback without crashing
        p2 = next((self.board_dir / "tasks").glob("T-002-*.md"))
        m2, b2 = kanban.parse_task_markdown(p2.read_text(encoding="utf-8"))
        m2["status"] = "statut_exotique"
        kanban.write_task_record(p2, m2, b2)
        ren = run_cli(self.board_dir, ["render", "--force-md"])
        self.assertEqual(ren.returncode, 0, ren.stderr)
        html_text = (self.board_dir / "kanban.html").read_text(encoding="utf-8")
        self.assertIn("statut_exotique", html_text)
        self.assertIn("__unknown__", html_text)

    def test_16_attachments_cli_and_serve_upload_security(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init", "--title", "CANARY_TEST_Attach"]).returncode, 0)
        self.assertEqual(run_cli(self.board_dir, ["add", "--title", "CANARY_TEST_With_File", "--status", "a_faire"]).returncode, 0)

        sample_file = Path(self._tmp.name) / "CANARY_TEST_note.txt"
        sample_file.write_text("Contenu pièce jointe UTF-8", encoding="utf-8")

        att_res = run_cli(self.board_dir, ["attach", "T-001", "--file", str(sample_file)])
        self.assertEqual(att_res.returncode, 0, att_res.stderr)
        card = json.loads(run_cli(self.board_dir, ["show", "T-001"]).stdout)
        self.assertEqual(len(card["attachments"]), 1)
        self.assertTrue(card["attachments"][0].startswith("attachments/T-001/"))

        # Reject unsafe extension (.html) & path traversal ref (../secret)
        bad_html = Path(self._tmp.name) / "bad.html"
        bad_html.write_text("<script>alert(1)</script>", encoding="utf-8")
        self.assertNotEqual(run_cli(self.board_dir, ["attach", "T-001", "--file", str(bad_html)]).returncode, 0)
        self.assertNotEqual(run_cli(self.board_dir, ["attach", "T-001", "--ref", "../secret.txt"]).returncode, 0)

        # Test serve upload + GET attachment + detach
        httpd, token = kanban.create_serve_httpd(self.board_dir, port=0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            b64_ok = base64.b64encode(b"hello kanban").decode("ascii")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/upload",
                data=json.dumps({"id": "T-001", "filename": "spec.md", "content_base64": b64_ok}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Kanban-Token": token},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                up_data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(up_data["result"]["attachment"], "attachments/T-001/spec.md")

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/attachments/T-001/spec.md", timeout=5) as gresp:
                self.assertEqual(gresp.status, 200)
                self.assertEqual(gresp.headers.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(gresp.read(), b"hello kanban")

            # Upload of .svg must be rejected (400)
            bad_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/upload",
                data=json.dumps({"id": "T-001", "filename": "evil.svg", "content_base64": b64_ok}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Kanban-Token": token},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req, timeout=5)
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()

            # Detach cleans up file and metadata
            det = run_cli(self.board_dir, ["detach", "T-001", "spec.md"])
            self.assertEqual(det.returncode, 0, det.stderr)
            self.assertFalse((self.board_dir / "attachments" / "T-001" / "spec.md").exists())
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_17_serve_port_fallback_strict_and_open_flag(self) -> None:
        self.assertEqual(run_cli(self.board_dir, ["init", "--title", "CANARY_TEST_ServeFallback"]).returncode, 0)
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _NoopHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

        blocker = ThreadingHTTPServer(("127.0.0.1", 0), _NoopHandler)
        busy_port = blocker.server_address[1]
        try:
            httpd, _ = kanban.create_serve_httpd(self.board_dir, port=busy_port, allow_port_fallback=True)
            try:
                self.assertGreater(httpd.server_address[1], 0)
                self.assertNotEqual(httpd.server_address[1], busy_port)
            finally:
                httpd.server_close()

            with self.assertRaises(kanban.KanbanError):
                kanban.create_serve_httpd(self.board_dir, port=busy_port, allow_port_fallback=False)

            parsed = kanban.build_parser().parse_args(["serve", "--port", "0", "--open", "--strict-port"])
            self.assertTrue(parsed.open_browser)
            self.assertTrue(parsed.strict_port)
        finally:
            blocker.server_close()


if __name__ == "__main__":
    unittest.main()
