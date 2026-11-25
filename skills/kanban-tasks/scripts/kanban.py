#!/usr/bin/env python3
"""Moteur CLI déterministe (stdlib uniquement) pour la skill kanban-tasks."""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import statistics
import sys
import tempfile
import time
import unicodedata
from urllib.parse import urlparse
import webbrowser

sys.dont_write_bytecode = True

if os.name == "nt":
    import msvcrt
else:
    import fcntl


PRIORITIES = ("basse", "normale", "haute", "urgente")
ROLES = ("backlog", "todo", "in_progress", "review", "done")
WINDOWS_RESERVED_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

DEFAULT_COLUMNS = [
    {"id": "idees", "label": "Idées", "wip_limit": None, "role": "backlog"},
    {"id": "a_faire", "label": "À faire", "wip_limit": 10, "role": "todo"},
    {"id": "en_cours", "label": "En cours", "wip_limit": 3, "role": "in_progress"},
    {"id": "en_revue", "label": "En revue", "wip_limit": 5, "role": "review"},
    {"id": "termine", "label": "Terminé", "wip_limit": None, "role": "done"},
]

DEFAULT_HORIZONS = [
    {"id": "cette_semaine", "label": "Cette semaine"},
    {"id": "ce_mois", "label": "Ce mois-ci"},
    {"id": "plus_tard", "label": "Plus tard"},
]

DEFAULT_LABELS = {
    "category": "Catégorie",
    "tag": "Tag",
}

FIELD_ORDER = [
    "id",
    "title",
    "status",
    "priority",
    "tags",
    "assignee",
    "claimed_at",
    "category",
    "horizon",
    "start_date",
    "due_date",
    "defer_until",
    "depends_on",
    "attachments",
    "blocked",
    "blocked_reason",
    "created_at",
    "updated_at",
    "history",
]

REQUIRED_FIELDS = ("id", "title", "status", "priority", "created_at", "updated_at")
SAFE_UNQUOTED_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_BOOL_LIKE = {"true", "false", "yes", "no", "on", "off", "null", "none", "~"}
HISTORY_LINE_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*\|\s*([^\s|]+)\s*->\s*([^\s|]+)\s*\|\s*(.*?)\s*$"
)
CHECKBOX_RE = re.compile(r"^(\s*[-*]\s+\[)([ xX])(\]\s+.*)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)$")
SAFE_ATTACHMENT_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md", ".csv", ".json"}
RASTER_ATTACHMENT_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class KanbanError(Exception):
    """Erreur métier ou de validation du tableau Kanban."""


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def format_utc(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> dt.datetime:
    if not isinstance(value, str) or not ISO_UTC_RE.match(value.strip()):
        raise KanbanError(f"Horodatage UTC invalide : {value!r}")
    norm = value.strip()
    if norm.endswith("Z"):
        norm = norm[:-1] + "+00:00"
    return dt.datetime.fromisoformat(norm).astimezone(dt.timezone.utc)


def parse_date(value: str, field_name: str = "date") -> dt.date:
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        raise KanbanError(f"Date {field_name} invalide (attendu YYYY-MM-DD) : {value!r}")
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise KanbanError(f"Date {field_name} invalide : {value!r}") from exc


def resolve_ref_datetime(today_str: str | None) -> tuple[dt.date, dt.datetime]:
    if today_str:
        day = parse_date(today_str, "today")
        ref_dt = dt.datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=dt.timezone.utc)
        return day, ref_dt
    now = utc_now()
    return now.date(), now


def slugify(text: str, max_len: int = 50) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    if max_len > 0:
        cleaned = cleaned[:max_len].strip("-")
    if not cleaned or cleaned in WINDOWS_RESERVED_STEMS:
        cleaned = f"tache-{cleaned}" if cleaned else "tache"
    return cleaned


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def safe_task_path(base_dir: Path, filename: str) -> Path:
    resolved_base = base_dir.resolve()
    target = (resolved_base / filename).resolve()
    if target.parent != resolved_base:
        raise KanbanError(f"Chemin de tâche non autorisé : {filename}")
    return target


class BoardLock:
    """Verrou fichier multi-processus cross-plateforme sur .kanban.lock."""

    def __init__(self, board_dir: Path, timeout: float = 10.0) -> None:
        self.lock_path = board_dir / ".kanban.lock"
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> "BoardLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            while True:
                try:
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return self
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        raise KanbanError(
                            f"Impossible d'acquérir le verrou {self.lock_path} après {self.timeout:.1f}s"
                        )
                    time.sleep(0.02)
        except Exception:
            os.close(fd)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._fd is not None:
            try:
                if os.name == "nt":
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def _parse_yaml_scalar(raw: str):
    val = raw.strip()
    if val == "" or val in ("null", "Null", "NULL", "~"):
        return ""
    if val in ("true", "True", "TRUE"):
        return True
    if val in ("false", "False", "FALSE"):
        return False
    if val.startswith('"'):
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError as exc:
            raise KanbanError(f"Chaîne YAML entre guillemets invalide : {raw!r}") from exc
        if not isinstance(parsed, str):
            raise KanbanError(f"Type YAML inattendu : {raw!r}")
        return parsed
    if val.startswith("'"):
        if len(val) < 2 or not val.endswith("'"):
            raise KanbanError(f"Chaîne YAML entre apostrophes non fermée : {raw!r}")
        return val[1:-1].replace("''", "'")
    if val.startswith("[") and val.endswith("]"):
        return _parse_inline_list(val)
    return val


def _parse_inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            return [str(x) for x in loaded]
    except json.JSONDecodeError:
        pass
    items: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    i = 0
    while i < len(inner):
        ch = inner[i]
        if in_quote:
            buf.append(ch)
            if ch == "\\" and in_quote == '"' and i + 1 < len(inner):
                i += 1
                buf.append(inner[i])
            elif ch == in_quote:
                if in_quote == "'" and i + 1 < len(inner) and inner[i + 1] == "'":
                    i += 1
                    buf.append(inner[i])
                else:
                    in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                buf.append(ch)
            elif ch == ",":
                token = "".join(buf).strip()
                if token:
                    items.append(str(_parse_yaml_scalar(token)))
                buf = []
            else:
                buf.append(ch)
        i += 1
    if in_quote:
        raise KanbanError(f"Liste YAML inline mal fermée : {raw!r}")
    token = "".join(buf).strip()
    if token:
        items.append(str(_parse_yaml_scalar(token)))
    return items


def parse_task_markdown(text: str, source_name: str = "<carte>") -> tuple[dict, str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise KanbanError(f"{source_name} : frontmatter YAML manquant en tête de fichier")
    lines = normalized.split("\n")
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise KanbanError(f"{source_name} : délimiteur de fin '---' du frontmatter introuvable")

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])
    if body.startswith("\n"):
        body = body[1:]

    meta: dict = {}
    current_list_key: str | None = None
    for raw_line in fm_lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s+-\s+", raw_line) or raw_line.strip() == "-":
            if current_list_key is None:
                raise KanbanError(f"{source_name} : élément de liste YAML sans clé parente ({raw_line!r})")
            item_raw = raw_line.lstrip()[1:].strip()
            meta[current_list_key].append(str(_parse_yaml_scalar(item_raw)))
            continue
        if raw_line[0].isspace():
            raise KanbanError(f"{source_name} : indentation YAML non plate non supportée ({raw_line!r})")
        if ":" not in raw_line:
            raise KanbanError(f"{source_name} : ligne YAML invalide sans ':' ({raw_line!r})")
        key, rest = raw_line.split(":", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z0-9_]+$", key):
            raise KanbanError(f"{source_name} : nom de clé YAML invalide ({key!r})")
        rest_stripped = rest.strip()
        if rest_stripped == "":
            meta[key] = []
            current_list_key = key
        else:
            parsed_val = _parse_yaml_scalar(rest_stripped)
            meta[key] = parsed_val
            current_list_key = None

    return meta, body


def _format_yaml_scalar(value, force_quote: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value)
    if not force_quote and s and SAFE_UNQUOTED_RE.match(s) and s.lower() not in SAFE_BOOL_LIKE:
        if not re.match(r"^-?\d+(?:\.\d+)?$", s):
            return s
    return json.dumps(s, ensure_ascii=False)


def serialize_task_markdown(meta: dict, body: str) -> str:
    lines = ["---"]
    ordered_keys = [k for k in FIELD_ORDER if k in meta] + [k for k in meta if k not in FIELD_ORDER]
    force_quote_keys = {
        "title", "assignee", "claimed_at", "category",
        "start_date", "due_date", "defer_until", "blocked_reason",
        "created_at", "updated_at",
    }
    for key in ordered_keys:
        val = meta[key]
        if val is None or val == "" or val == [] or (key == "blocked" and val is False):
            continue
        if isinstance(val, list):
            lines.append(f"{key}:")
            for item in val:
                lines.append(f"  - {_format_yaml_scalar(item, force_quote=(key == 'history'))}")
        else:
            lines.append(f"{key}: {_format_yaml_scalar(val, force_quote=(key in force_quote_keys))}")
    lines.append("---")
    clean_body = body.replace("\r\n", "\n").replace("\r", "\n").lstrip("\n")
    if clean_body:
        return "\n".join(lines) + "\n\n" + clean_body.rstrip("\n") + "\n"
    return "\n".join(lines) + "\n"


def load_config(board_dir: Path) -> dict:
    cfg_path = board_dir / "kanban.json"
    if not cfg_path.exists():
        raise KanbanError(f"Tableau non initialisé ({cfg_path} introuvable). Lancez d'abord 'init'.")
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KanbanError(f"Configuration kanban.json illisible : {exc}") from exc
    if not isinstance(cfg, dict) or not isinstance(cfg.get("columns"), list) or not cfg["columns"]:
        raise KanbanError("kanban.json invalide : 'columns' doit être une liste non vide")
    return cfg


def save_config(board_dir: Path, cfg: dict) -> None:
    atomic_write_text(board_dir / "kanban.json", json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def get_column_map(cfg: dict) -> dict[str, dict]:
    return {col["id"]: col for col in cfg["columns"]}


def get_horizon_map(cfg: dict) -> dict[str, dict]:
    return {h["id"]: h for h in cfg.get("horizons", DEFAULT_HORIZONS)}


def get_labels(cfg: dict) -> dict[str, str]:
    raw = cfg.get("labels") if isinstance(cfg.get("labels"), dict) else {}
    cat_lbl = str(raw.get("category") or DEFAULT_LABELS["category"]).strip() or DEFAULT_LABELS["category"]
    tag_lbl = str(raw.get("tag") or DEFAULT_LABELS["tag"]).strip() or DEFAULT_LABELS["tag"]
    return {"category": cat_lbl, "tag": tag_lbl}


def extract_id_number(task_id: str, prefix: str) -> int | None:
    m = re.match(rf"^{re.escape(prefix)}-(\d+)$", str(task_id))
    return int(m.group(1)) if m else None


def format_task_id(num: int, prefix: str) -> str:
    return f"{prefix}-{num:03d}"


def compute_next_id_number(board_dir: Path, cfg: dict) -> int:
    prefix = cfg.get("id_prefix", "T")
    highest = max(0, int(cfg.get("next_id", 1)) - 1)
    for folder_name in ("tasks", "archive"):
        folder = board_dir / folder_name
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
            m = re.match(rf"^{re.escape(prefix)}-(\d+)(?:-|$)", md_file.stem)
            if m:
                highest = max(highest, int(m.group(1)))
            with contextlib.suppress(Exception):
                meta, _ = parse_task_markdown(md_file.read_text(encoding="utf-8"), md_file.name)
                n = extract_id_number(str(meta.get("id", "")), prefix)
                if n is not None:
                    highest = max(highest, n)
    return highest + 1


def load_tasks_from_folder(folder: Path, archived: bool) -> list[dict]:
    records: list[dict] = []
    if not folder.exists():
        return records
    for path in sorted(folder.glob("*.md")):
        meta, body = parse_task_markdown(path.read_text(encoding="utf-8"), str(path))
        records.append({
            "meta": meta,
            "body": body,
            "path": path,
            "filename": path.name,
            "archived": archived,
        })
    return records


def load_all_records(board_dir: Path) -> tuple[list[dict], list[dict]]:
    active = load_tasks_from_folder(board_dir / "tasks", archived=False)
    archived = load_tasks_from_folder(board_dir / "archive", archived=True)
    return active, archived


def find_task_record(records: list[dict], task_id: str) -> dict:
    needle = task_id.strip().upper()
    for rec in records:
        if str(rec["meta"].get("id", "")).upper() == needle:
            return rec
    raise KanbanError(f"Carte introuvable : {task_id}")


def parse_history_entries(history: list[str] | None) -> list[dict]:
    entries: list[dict] = []
    for line in history or []:
        m = HISTORY_LINE_RE.match(str(line))
        if m:
            entries.append({
                "at": m.group(1),
                "from": m.group(2),
                "to": m.group(3),
                "actor": m.group(4),
                "raw": line,
            })
    return entries


def extract_subtasks(body: str) -> tuple[int, int, list[dict]]:
    items: list[dict] = []
    done = 0
    for idx, line in enumerate(body.replace("\r\n", "\n").split("\n")):
        m = CHECKBOX_RE.match(line)
        if m:
            is_done = m.group(2).lower() == "x"
            text = m.group(3)[2:].strip()
            if is_done:
                done += 1
            items.append({"index": len(items) + 1, "line_idx": idx, "done": is_done, "text": text})
    return done, len(items), items


def compute_cycle_metrics(meta: dict, col_map: dict[str, dict]) -> dict:
    entries = parse_history_entries(meta.get("history", []))
    created_at = str(meta.get("created_at", ""))
    first_in_progress: dt.datetime | None = None
    last_done: dt.datetime | None = None
    rework_count = 0

    for entry in entries:
        to_role = col_map.get(entry["to"], {}).get("role")
        from_role = col_map.get(entry["from"], {}).get("role")
        ts = parse_utc(entry["at"])
        if to_role == "in_progress" and first_in_progress is None:
            first_in_progress = ts
        if to_role == "done":
            last_done = ts
        if from_role == "review" and to_role == "in_progress":
            rework_count += 1

    cycle_hours: float | None = None
    lead_hours: float | None = None
    if last_done is not None:
        if first_in_progress is not None and last_done >= first_in_progress:
            cycle_hours = round((last_done - first_in_progress).total_seconds() / 3600.0, 2)
        if created_at:
            with contextlib.suppress(KanbanError):
                created_dt = parse_utc(created_at)
                if last_done >= created_dt:
                    lead_hours = round((last_done - created_dt).total_seconds() / 3600.0, 2)

    return {
        "cycle_time_hours": cycle_hours,
        "lead_time_hours": lead_hours,
        "rework_count": rework_count,
    }


def compute_entered_status_at(meta: dict) -> str:
    status = str(meta.get("status", ""))
    entries = parse_history_entries(meta.get("history", []))
    for entry in reversed(entries):
        if entry["to"] == status:
            return entry["at"]
    return str(meta.get("created_at", ""))


def detect_dependency_cycles(tasks_by_id: dict[str, dict]) -> list[list[str]]:
    state: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        state[node] = 1
        stack.append(node)
        rec = tasks_by_id.get(node)
        if rec:
            for dep in rec["meta"].get("depends_on", []) or []:
                dep_id = str(dep)
                if dep_id not in tasks_by_id:
                    continue
                st = state.get(dep_id, 0)
                if st == 0:
                    dfs(dep_id)
                elif st == 1:
                    start = stack.index(dep_id)
                    cycles.append(stack[start:] + [dep_id])
        stack.pop()
        state[node] = 2

    for tid in sorted(tasks_by_id):
        if state.get(tid, 0) == 0:
            dfs(tid)
    return cycles


def enrich_records(
    active: list[dict],
    archived: list[dict],
    cfg: dict,
    today_str: str | None = None,
) -> list[dict]:
    ref_date, ref_dt = resolve_ref_datetime(today_str)
    col_map = get_column_map(cfg)
    stale_days = int(cfg.get("stale_days", 7))

    done_ids: set[str] = set()
    all_records = active + archived
    for rec in all_records:
        tid = str(rec["meta"].get("id", ""))
        col_role = col_map.get(str(rec["meta"].get("status", "")), {}).get("role")
        if rec["archived"] or col_role == "done":
            done_ids.add(tid)

    enriched: list[dict] = []
    for rec in all_records:
        meta = rec["meta"]
        body = rec["body"]
        tid = str(meta.get("id", ""))
        status = str(meta.get("status", ""))
        col = col_map.get(status, {"id": status, "label": status, "role": "todo"})
        role = col.get("role", "todo")

        sub_done, sub_total, subtasks = extract_subtasks(body)
        entered_at_str = compute_entered_status_at(meta)
        age_days = 0.0
        age_hours = 0.0
        if entered_at_str:
            with contextlib.suppress(KanbanError):
                entered_dt = parse_utc(entered_at_str)
                elapsed_sec = max(0.0, (ref_dt - entered_dt).total_seconds())
                age_days = round(elapsed_sec / 86400.0, 2)
                age_hours = round(elapsed_sec / 3600.0, 2)

        defer_until = str(meta.get("defer_until", "") or "")
        is_deferred = False
        if defer_until:
            with contextlib.suppress(KanbanError):
                is_deferred = parse_date(defer_until, "defer_until") > ref_date

        due_date = str(meta.get("due_date", "") or "")
        is_overdue = False
        if due_date and role != "done" and not rec["archived"]:
            with contextlib.suppress(KanbanError):
                is_overdue = parse_date(due_date, "due_date") < ref_date

        is_stale = (
            not rec["archived"]
            and role in ("in_progress", "review")
            and not is_deferred
            and age_days >= stale_days
        )

        deps = [str(d) for d in (meta.get("depends_on") or [])]
        unmet_deps = [d for d in deps if d not in done_ids]
        implicitly_blocked = len(unmet_deps) > 0 and role != "done" and not rec["archived"]
        explicit_blocked = bool(meta.get("blocked", False))
        flow_metrics = compute_cycle_metrics(meta, col_map)

        enriched.append({
            **rec,
            "id": tid,
            "title": str(meta.get("title", "")),
            "status": status,
            "status_label": col.get("label", status),
            "role": role,
            "priority": str(meta.get("priority", "normale")),
            "tags": [str(t) for t in (meta.get("tags") or [])],
            "assignee": str(meta.get("assignee", "") or ""),
            "claimed_at": str(meta.get("claimed_at", "") or ""),
            "category": str(meta.get("category", "") or ""),
            "horizon": str(meta.get("horizon", "") or ""),
            "start_date": str(meta.get("start_date", "") or ""),
            "due_date": due_date,
            "defer_until": defer_until,
            "depends_on": deps,
            "unmet_dependencies": unmet_deps,
            "unblocks": [],
            "attachments": [str(a) for a in (meta.get("attachments") or [])],
            "implicitly_blocked": implicitly_blocked,
            "blocked": explicit_blocked,
            "blocked_reason": str(meta.get("blocked_reason", "") or ""),
            "is_deferred": is_deferred,
            "is_overdue": is_overdue,
            "is_stale": is_stale,
            "entered_status_at": entered_at_str,
            "age_in_status_days": age_days,
            "age_in_status_hours": age_hours,
            "subtasks_done": sub_done,
            "subtasks_total": sub_total,
            "subtasks": subtasks,
            **flow_metrics,
        })

    unblocks_map: dict[str, list[str]] = {}
    for item in enriched:
        if item["archived"] or item["role"] == "done":
            continue
        for dep in item["depends_on"]:
            unblocks_map.setdefault(dep, []).append(item["id"])
    for item in enriched:
        item["unblocks"] = sorted(set(unblocks_map.get(item["id"], [])))

    return enriched


def write_task_record(path: Path, meta: dict, body: str) -> None:
    atomic_write_text(path, serialize_task_markdown(meta, body))


def check_kanban_md_drift(board_dir: Path, cfg: dict) -> tuple[bool, str]:
    md_path = board_dir / "KANBAN.md"
    recorded = str(cfg.get("last_rendered_md_sha256", "") or "")
    if not md_path.exists() or not recorded:
        return False, ""
    current = sha256_text(md_path.read_text(encoding="utf-8").replace("\r\n", "\n"))
    if current != recorded:
        return True, current
    return False, current


def render_kanban_markdown(board_dir: Path, cfg: dict, enriched: list[dict], ref_date: dt.date) -> str:
    obsidian = bool(cfg.get("obsidian_compat", True))
    active = [t for t in enriched if not t["archived"]]
    archived = [t for t in enriched if t["archived"]]
    horizon_labels = {h["id"]: h["label"] for h in cfg.get("horizons", DEFAULT_HORIZONS)}
    prio_rank = {"urgente": 0, "haute": 1, "normale": 2, "basse": 3}

    lines: list[str] = []
    if obsidian:
        lines.extend(["---", "kanban-plugin: board", "---", ""])
    lines.append(f"# {cfg.get('title', 'Tableau Kanban')}")
    lines.append("")
    lines.append(
        "<!-- Vue générée automatiquement par kanban-tasks depuis tasks/*.md. "
        "Si vous déplacez des cartes ici (ex. Obsidian), exécutez 'sync-md' avant le prochain 'render'. -->"
    )
    lines.append("")

    for col in cfg["columns"]:
        col_id = col["id"]
        lines.append(f"## {col['label']}")
        col_tasks = sorted(
            [t for t in active if t["status"] == col_id],
            key=lambda item: (prio_rank.get(item["priority"], 9), item["id"]),
        )
        for task in col_tasks:
            box = "[x]" if col.get("role") == "done" else "[ ]"
            rel_link = f"tasks/{task['filename']}"
            parts = [f"- {box} [{task['id']} · {task['title']}]({rel_link})"]
            if task["priority"] != "normale":
                parts.append(f"`prio:{task['priority']}`")
            if task["subtasks_total"] > 0:
                parts.append(f"`{task['subtasks_done']}/{task['subtasks_total']}`")
            if task["assignee"]:
                parts.append(f"@{task['assignee']}")
            if task["category"]:
                parts.append(f"[{task['category']}]")
            if task["horizon"]:
                h_label = horizon_labels.get(task["horizon"], task["horizon"])
                parts.append(f"horizon:{h_label}")
            if task["due_date"]:
                due_tag = "RETARD" if task["is_overdue"] else "échéance"
                parts.append(f"{due_tag}:{task['due_date']}")
            if task["blocked"]:
                reason = f" ({task['blocked_reason']})" if task["blocked_reason"] else ""
                parts.append(f"**BLOQUÉ{reason}**")
            if task["implicitly_blocked"]:
                parts.append(f"attente:{','.join(task['unmet_dependencies'])}")
            if task["is_deferred"]:
                parts.append(f"veille:{task['defer_until']}")
            if task["is_stale"]:
                parts.append(f"stagnante:{int(task['age_in_status_days'])}j")
            if task["attachments"]:
                parts.append(f"pièces:{len(task['attachments'])}")
            for tag in task["tags"]:
                parts.append(f"#{tag}")
            lines.append(" ".join(parts))
        lines.append("")

    if archived:
        lines.append(f"_Cartes archivées dans `archive/` : {len(archived)}_")
        lines.append("")

    if obsidian:
        settings_json = json.dumps({"kanban-plugin": "board", "show-checkboxes": True}, ensure_ascii=False)
        lines.extend(["%% kanban:settings", settings_json, "%%", ""])

    return "\n".join(lines).rstrip("\n") + "\n"


def safe_json_for_html(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False)
    return (
        raw.replace("</", "<\\/")
        .replace("<!--", "<\\!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def compute_board_stats(cfg: dict, enriched: list[dict]) -> dict:
    active = [t for t in enriched if not t["archived"]]
    archived = [t for t in enriched if t["archived"]]
    by_column = {
        col["id"]: {
            "id": col["id"],
            "label": col["label"],
            "role": col.get("role", "todo"),
            "wip_limit": col.get("wip_limit"),
            "count": sum(1 for t in active if t["status"] == col["id"]),
        }
        for col in cfg["columns"]
    }
    horizons = cfg.get("horizons", DEFAULT_HORIZONS)
    by_horizon = {
        h["id"]: sum(1 for t in active if t["horizon"] == h["id"])
        for h in horizons
    }
    by_horizon["sans_horizon"] = sum(1 for t in active if not t["horizon"])

    cycle_vals = [t["cycle_time_hours"] for t in enriched if t["cycle_time_hours"] is not None]
    lead_vals = [t["lead_time_hours"] for t in enriched if t["lead_time_hours"] is not None]
    ages = [t["age_in_status_days"] for t in active if t["role"] != "done"]

    wip_alerts = []
    for col in cfg["columns"]:
        limit = col.get("wip_limit")
        cnt = by_column[col["id"]]["count"]
        if limit is not None and cnt > int(limit):
            wip_alerts.append({
                "column_id": col["id"],
                "label": col["label"],
                "count": cnt,
                "wip_limit": int(limit),
            })

    return {
        "active_count": len(active),
        "archived_count": len(archived),
        "blocked_count": sum(1 for t in active if t["blocked"]),
        "implicitly_blocked_count": sum(1 for t in active if t["implicitly_blocked"]),
        "overdue_count": sum(1 for t in active if t["is_overdue"]),
        "stale_count": sum(1 for t in active if t["is_stale"]),
        "deferred_count": sum(1 for t in active if t["is_deferred"]),
        "rework_total": sum(int(t["rework_count"]) for t in enriched),
        "avg_cycle_time_hours": round(statistics.mean(cycle_vals), 2) if cycle_vals else None,
        "median_cycle_time_hours": round(statistics.median(cycle_vals), 2) if cycle_vals else None,
        "avg_lead_time_hours": round(statistics.mean(lead_vals), 2) if lead_vals else None,
        "median_lead_time_hours": round(statistics.median(lead_vals), 2) if lead_vals else None,
        "max_work_item_age_days": max(ages) if ages else 0.0,
        "by_column": by_column,
        "by_horizon": by_horizon,
        "wip_alerts": wip_alerts,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<style>
:root {
  --bg: #f4f6f9;
  --surface: #ffffff;
  --surface-alt: #eef2f6;
  --border: #d7dee8;
  --text: #16202a;
  --muted: #5b6878;
  --accent: #2563eb;
  --accent-soft: #dbeafe;
  --danger: #dc2626;
  --danger-soft: #fee2e2;
  --warn: #d97706;
  --warn-soft: #fef3c7;
  --ok: #059669;
  --ok-soft: #d1fae5;
  --purple: #7c3aed;
  --purple-soft: #ede9fe;
  --shadow: 0 2px 8px rgba(15, 23, 42, 0.07);
}
[data-theme="dark"] {
  --bg: #0f172a;
  --surface: #1e293b;
  --surface-alt: #273449;
  --border: #334155;
  --text: #f1f5f9;
  --muted: #94a3b8;
  --accent: #60a5fa;
  --accent-soft: rgba(59, 130, 246, 0.2);
  --danger: #f87171;
  --danger-soft: rgba(239, 68, 68, 0.2);
  --warn: #fbbf24;
  --warn-soft: rgba(245, 158, 11, 0.2);
  --ok: #34d399;
  --ok-soft: rgba(16, 185, 129, 0.2);
  --purple: #a78bfa;
  --purple-soft: rgba(139, 92, 246, 0.2);
  --shadow: 0 2px 10px rgba(0, 0, 0, 0.35);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
}
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 14px 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}
.title-block h1 { margin: 0; font-size: 1.25rem; }
.title-block .subtitle { font-size: 0.85rem; color: var(--muted); }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
button, select, input {
  font: inherit;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
}
button { cursor: pointer; }
button:hover { border-color: var(--accent); }
button.active {
  background: var(--accent);
  color: #ffffff;
  border-color: var(--accent);
}
.filters {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 10px 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.filters input[type="search"] { min-width: 210px; flex: 1 1 210px; max-width: 320px; }
.chip-btn { font-size: 0.82rem; padding: 4px 9px; border-radius: 999px; background: var(--surface-alt); }
.chip-btn.active { background: var(--accent); color: #ffffff; }
.stats-panel {
  margin: 14px 20px 0;
  padding: 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.stats-panel.hidden { display: none; }
.stat-box { background: var(--surface-alt); border-radius: 8px; padding: 10px; }
.stat-box .k { font-size: 0.78rem; color: var(--muted); }
.stat-box .v { font-size: 1.15rem; font-weight: 700; margin-top: 2px; }
main { padding: 16px 20px 28px; }
.board-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(205px, 1fr));
  gap: 10px;
  align-items: start;
}
.column {
  background: var(--surface-alt);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px;
  min-height: 160px;
  transition: border-color 0.15s, background-color 0.15s;
}
.column.drag-over {
  border-color: var(--accent);
  border-style: dashed;
  background: var(--accent-soft);
}
.col-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-weight: 700;
  font-size: 0.95rem;
}
.wip-badge {
  font-size: 0.78rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--surface);
  border: 1px solid var(--border);
}
.wip-badge.over { background: var(--danger-soft); color: var(--danger); border-color: var(--danger); }
.swimlane { margin-bottom: 20px; }
.swimlane-title {
  font-size: 0.95rem;
  font-weight: 700;
  padding: 6px 10px;
  background: var(--surface);
  border-left: 4px solid var(--accent);
  border-radius: 6px;
  margin-bottom: 10px;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 10px;
  margin-bottom: 9px;
  box-shadow: var(--shadow);
  cursor: pointer;
}
.card:hover { border-color: var(--accent); }
.card.dragging { opacity: 0.45; }
.card[draggable="true"] { cursor: grab; }
.card.prio-urgente { border-left: 4px solid var(--danger); }
.card.prio-haute { border-left: 4px solid var(--warn); }
.card.prio-basse { opacity: 0.88; }
.card-top { display: flex; justify-content: space-between; align-items: center; gap: 6px; margin-bottom: 5px; }
.card-id { font-size: 0.76rem; font-weight: 700; color: var(--accent); }
.card-title {
  font-weight: 600;
  font-size: 0.92rem;
  margin-bottom: 6px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.badges { display: flex; flex-wrap: wrap; gap: 4px; }
.badge {
  font-size: 0.72rem;
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--surface-alt);
  color: var(--muted);
}
.badge.danger { background: var(--danger-soft); color: var(--danger); font-weight: 600; }
.badge.warn { background: var(--warn-soft); color: var(--warn); font-weight: 600; }
.badge.ok { background: var(--ok-soft); color: var(--ok); }
.badge.purple { background: var(--purple-soft); color: var(--purple); }
.badge.accent { background: var(--accent-soft); color: var(--accent); }
.copy-mini {
  font-size: 0.72rem;
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--surface-alt);
}
.timeline-layout { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 960px) { .timeline-layout { grid-template-columns: 1fr; } }
.timeline-box {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px;
  overflow-x: auto;
}
.tl-row {
  display: grid;
  grid-template-columns: 210px 1fr;
  gap: 10px;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
.tl-track { position: relative; height: 24px; background: var(--surface-alt); border-radius: 6px; }
.tl-bar {
  position: absolute;
  top: 3px;
  height: 18px;
  border-radius: 5px;
  background: var(--accent);
  color: #ffffff;
  font-size: 0.72rem;
  padding: 0 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.tl-bar.overdue { background: var(--danger); }
.tl-bar.done { background: var(--ok); }
.tl-today { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--danger); opacity: 0.7; }
.week-strip { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 99;
}
.modal-backdrop.hidden { display: none; }
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  max-width: 700px;
  width: 100%;
  max-height: 88vh;
  overflow-y: auto;
  padding: 18px;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: start;
  position: sticky;
  top: -18px;
  background: var(--surface);
  z-index: 4;
  padding: 12px 0 10px;
  margin-top: -12px;
  border-bottom: 1px solid var(--border);
}
.modal-section { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
.modal-body-pre {
  white-space: pre-wrap;
  background: var(--surface-alt);
  padding: 10px;
  border-radius: 8px;
  font-size: 0.88rem;
}
.order-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.toast {
  position: fixed;
  bottom: 18px;
  right: 18px;
  background: var(--text);
  color: var(--surface);
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 0.85rem;
  z-index: 120;
}
.toast.hidden { display: none; }
.empty-msg { color: var(--muted); font-size: 0.86rem; padding: 8px 2px; }
.hidden { display: none !important; }
.mode-badge {
  font-size: 0.78rem;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--surface-alt);
  color: var(--muted);
  border: 1px solid var(--border);
}
.mode-badge.live {
  background: var(--ok-soft);
  color: var(--ok);
  border-color: var(--ok);
}
.btn-primary {
  background: var(--accent);
  color: #ffffff;
  border-color: var(--accent);
  font-weight: 600;
}
.subtask-list { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.subtask-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  background: var(--surface-alt);
  border-radius: 6px;
  font-size: 0.88rem;
}
.subtask-row.done span { text-decoration: line-through; color: var(--muted); }
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.form-field { display: flex; flex-direction: column; gap: 4px; font-size: 0.84rem; }
.form-field.full { grid-column: 1 / -1; }
.form-field label { font-weight: 600; color: var(--muted); }
.form-field textarea {
  font: inherit;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  min-height: 96px;
  resize: vertical;
}
.form-inline { display: flex; align-items: center; gap: 8px; flex-direction: row; }
.tax-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.tax-row input { flex: 1; min-width: 140px; }
.card.is-done .card-title { text-decoration: line-through; color: var(--muted); opacity: 0.72; }
.card.is-blocked { border-color: var(--danger); }
.card-context {
  margin: 4px 0 7px;
  padding: 5px 8px;
  border-radius: 6px;
  font-size: 0.78rem;
  background: var(--danger-soft);
  color: var(--danger);
  font-weight: 600;
}
.dep-btn {
  min-height: 24px;
  padding: 2px 7px;
  font-size: 0.72rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface-alt);
  color: var(--text);
}
.dep-btn.warn { background: var(--warn-soft); color: var(--warn); border-color: var(--warn); font-weight: 600; }
.dep-btn.accent { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); font-weight: 600; }
.dep-drawer {
  margin-top: 7px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--surface-alt);
  font-size: 0.78rem;
}
.dep-drawer ul { margin: 4px 0 0; padding-left: 16px; }
.dep-drawer li { margin-bottom: 2px; }
.dep-link { cursor: pointer; text-decoration: underline; color: var(--accent); background: none; border: none; padding: 0; font: inherit; text-align: left; }
.modal-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin: 10px 0 6px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.modal-tab { font-size: 0.84rem; padding: 5px 10px; border-radius: 8px; background: var(--surface-alt); }
.modal-tab.active { background: var(--accent); color: #ffffff; border-color: var(--accent); }
.tab-panel.hidden { display: none; }
.dep-picker-list {
  max-height: 170px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px;
  background: var(--surface-alt);
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}
.dep-picker-item { display: flex; align-items: center; gap: 8px; font-size: 0.84rem; cursor: pointer; padding: 3px 4px; border-radius: 5px; }
.dep-picker-item:hover { background: var(--surface); }
.md-box {
  background: var(--surface-alt);
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 0.88rem;
  line-height: 1.5;
}
.md-box h1, .md-box h2, .md-box h3 { margin: 6px 0 4px; font-size: 0.98rem; }
.md-box p { margin: 4px 0; }
.md-box ul { margin: 4px 0; padding-left: 18px; }
.md-box pre { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 8px; overflow-x: auto; font-size: 0.82rem; }
.md-box code { background: var(--surface); padding: 1px 4px; border-radius: 4px; font-size: 0.84em; }
.att-list { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.att-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 5px 8px; background: var(--surface-alt); border-radius: 6px; font-size: 0.84rem; }
.att-row a { color: var(--accent); word-break: break-all; }
.btn-danger { background: var(--danger-soft); color: var(--danger); border-color: var(--danger); font-weight: 600; }
.delete-confirm-bar { margin-top: 10px; padding: 10px; border-radius: 8px; background: var(--danger-soft); border: 1px solid var(--danger); font-size: 0.85rem; }
.col-more-btn { width: 100%; margin-top: 4px; font-size: 0.8rem; background: var(--surface); }
</style>
</head>
<body>
<header>
  <div class="title-block">
    <h1 id="board-title">Tableau Kanban</h1>
    <div class="subtitle" id="board-subtitle"></div>
  </div>
  <div class="toolbar">
    <span id="mode-badge" class="mode-badge">Lecture seule</span>
    <button type="button" id="btn-new-card" class="btn-primary hidden">+ Nouvelle carte</button>
    <button type="button" id="btn-settings" class="hidden">Réglages</button>
    <button type="button" id="btn-refresh" class="hidden">Rafraîchir</button>
    <button type="button" class="view-tab active" data-view="kanban">Kanban</button>
    <button type="button" class="view-tab" data-view="horizons">Horizons</button>
    <button type="button" class="view-tab" data-view="timeline">Frise</button>
    <button type="button" id="toggle-swimlanes">Swimlanes : Non</button>
    <button type="button" id="toggle-stats">Statistiques</button>
    <button type="button" id="toggle-theme">Thème</button>
  </div>
</header>

<section class="filters">
  <input type="search" id="f-search" placeholder="Rechercher ID, titre, tag, note...">
  <select id="f-priority"><option value="">Priorité : Toutes</option></select>
  <select id="f-assignee"><option value="">Assignation : Tous</option></select>
  <select id="f-category"><option value="">Catégorie : Toutes</option></select>
  <select id="f-tag"><option value="">Tag : Tous</option></select>
  <button type="button" class="chip-btn" data-flag="blocked">Bloquées</button>
  <button type="button" class="chip-btn" data-flag="waiting">En attente</button>
  <button type="button" class="chip-btn" data-flag="overdue">En retard</button>
  <button type="button" class="chip-btn" data-flag="stale">Stagnantes</button>
  <button type="button" class="chip-btn" data-flag="deferred">En veille</button>
  <button type="button" class="chip-btn" data-flag="archived">Archivées</button>
  <button type="button" id="f-reset">Réinitialiser</button>
</section>

<section id="stats-panel" class="stats-panel hidden"></section>
<main id="app-main"></main>

<div id="modal-backdrop" class="modal-backdrop hidden">
  <div class="modal" role="dialog" aria-modal="true">
    <div class="modal-head">
      <div>
        <div id="m-id" class="card-id"></div>
        <h2 id="m-title" style="margin:4px 0 0;font-size:1.15rem;"></h2>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button type="button" id="btn-edit-card" class="hidden">Modifier la carte</button>
        <button type="button" id="btn-archive-card" class="hidden">Archiver</button>
        <button type="button" id="btn-delete-card" class="btn-danger hidden">Supprimer</button>
        <button type="button" id="m-close">Fermer</button>
      </div>
    </div>
    <div id="m-delete-bar" class="delete-confirm-bar hidden">
      <div id="m-delete-msg">Confirmer la suppression définitive de cette carte ?</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">
        <button type="button" id="btn-delete-confirm" class="btn-danger">Confirmer la suppression</button>
        <button type="button" id="btn-delete-force" class="btn-danger hidden">Forcer (retirer des dépendances)</button>
        <button type="button" id="btn-delete-cancel">Annuler</button>
      </div>
    </div>
    <div id="m-view-section">
      <div id="m-badges" class="badges" style="margin-top:8px;"></div>
      <div id="m-deps-section" class="modal-section hidden">
        <strong>Liens &amp; dépendances</strong>
        <div id="m-deps-list" style="margin-top:6px;font-size:0.86rem;"></div>
      </div>
      <div id="m-subtasks-wrap" class="modal-section hidden">
        <strong>Sous-tâches</strong>
        <div id="m-subtasks" class="subtask-list"></div>
      </div>
      <div id="m-atts-wrap" class="modal-section hidden">
        <strong>Pièces jointes &amp; références</strong>
        <div id="m-atts-list" class="att-list"></div>
      </div>
      <div class="modal-section">
        <strong>Ordres rapides pour l'assistant</strong>
        <div id="m-orders" class="order-actions"></div>
      </div>
      <div class="modal-section">
        <strong>Contenu &amp; notes</strong>
        <div id="m-body" class="md-box" style="margin-top:6px;"></div>
      </div>
      <div class="modal-section">
        <strong>Historique</strong>
        <ul id="m-history" style="padding-left:18px;margin:6px 0;font-size:0.84rem;"></ul>
      </div>
    </div>
    <form id="m-edit-section" class="modal-section hidden">
      <div class="modal-tabs" id="e-tabs">
        <button type="button" class="modal-tab active" data-tab="e-tab-general">Général</button>
        <button type="button" class="modal-tab" data-tab="e-tab-plan" id="e-tab-plan-btn">Planning (0)</button>
        <button type="button" class="modal-tab" data-tab="e-tab-links" id="e-tab-links-btn">Dépendances &amp; Pièces (0)</button>
      </div>
      <div id="e-tab-general" class="tab-panel">
        <div class="form-grid">
          <div class="form-field full"><label for="e-title">Titre</label><input type="text" id="e-title" required></div>
          <div class="form-field"><label for="e-status">Colonne</label><select id="e-status"></select></div>
          <div class="form-field"><label for="e-priority">Priorité</label><select id="e-priority"></select></div>
          <div class="form-field"><label for="e-category" id="lbl-e-category">Catégorie</label><input type="text" id="e-category" list="dl-categories"></div>
          <div class="form-field"><label for="e-assignee">Assigné</label><input type="text" id="e-assignee"></div>
          <div class="form-field full"><label for="e-tags" id="lbl-e-tags">Tags (séparés par virgule)</label><input type="text" id="e-tags"></div>
          <div class="form-field full form-inline">
            <input type="checkbox" id="e-blocked">
            <label for="e-blocked" style="margin:0;">Bloqué</label>
            <input type="text" id="e-blocked-reason" placeholder="Motif du blocage" style="flex:1;">
          </div>
          <div class="form-field full">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <label for="e-body">Contenu / Notes / Sous-tâches (- [ ] ...)</label>
              <button type="button" id="e-preview-toggle" class="copy-mini">Aperçu Markdown</button>
            </div>
            <textarea id="e-body"></textarea>
            <div id="e-body-preview" class="md-box hidden"></div>
          </div>
        </div>
      </div>
      <div id="e-tab-plan" class="tab-panel hidden">
        <div class="form-grid">
          <div class="form-field"><label for="e-horizon">Horizon</label><select id="e-horizon"></select></div>
          <div class="form-field"><label for="e-start">Date de début</label><input type="date" id="e-start"></div>
          <div class="form-field"><label for="e-due">Échéance</label><input type="date" id="e-due"></div>
          <div class="form-field"><label for="e-defer">En veille jusqu'au</label><input type="date" id="e-defer"></div>
        </div>
      </div>
      <div id="e-tab-links" class="tab-panel hidden">
        <div class="form-field">
          <label for="e-dep-search">Dépendances (attend les cartes cochées)</label>
          <input type="search" id="e-dep-search" placeholder="Filtrer par ID ou titre...">
          <div id="e-dep-picker" class="dep-picker-list"></div>
        </div>
        <div class="form-field" style="margin-top:10px;">
          <label>Pièces jointes &amp; références</label>
          <div id="e-atts-list" class="att-list"></div>
          <div class="tax-row" style="margin-top:6px;">
            <input type="file" id="e-upload-file" accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.md,.csv,.json">
            <button type="button" id="e-upload-btn">Joindre fichier</button>
          </div>
          <div class="tax-row">
            <input type="text" id="e-attach-ref-input" placeholder="Ou ajouter une référence relative (ex. docs/spec.md)">
            <button type="button" id="e-attach-ref-btn">+ Référence</button>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
        <button type="button" id="e-cancel">Annuler</button>
        <button type="submit" class="btn-primary">Enregistrer</button>
      </div>
    </form>
  </div>
</div>

<div id="create-modal-backdrop" class="modal-backdrop hidden">
  <div class="modal" role="dialog" aria-modal="true">
    <div class="modal-head">
      <h2 style="margin:0;font-size:1.15rem;">Nouvelle carte</h2>
      <button type="button" id="c-close">Fermer</button>
    </div>
    <form id="c-form" class="modal-section">
      <div class="modal-tabs" id="c-tabs">
        <button type="button" class="modal-tab active" data-tab="c-tab-general">Général</button>
        <button type="button" class="modal-tab" data-tab="c-tab-plan" id="c-tab-plan-btn">Planning (0)</button>
        <button type="button" class="modal-tab" data-tab="c-tab-links" id="c-tab-links-btn">Dépendances (0)</button>
      </div>
      <div id="c-tab-general" class="tab-panel">
        <div class="form-grid">
          <div class="form-field full"><label for="c-title">Titre</label><input type="text" id="c-title" required></div>
          <div class="form-field"><label for="c-status">Colonne</label><select id="c-status"></select></div>
          <div class="form-field"><label for="c-priority">Priorité</label><select id="c-priority"></select></div>
          <div class="form-field"><label for="c-category" id="lbl-c-category">Catégorie</label><input type="text" id="c-category" list="dl-categories"></div>
          <div class="form-field"><label for="c-assignee">Assigné</label><input type="text" id="c-assignee"></div>
          <div class="form-field full"><label for="c-tags" id="lbl-c-tags">Tags (séparés par virgule)</label><input type="text" id="c-tags"></div>
          <div class="form-field full"><label for="c-subtasks">Sous-tâches (1 par ligne)</label><textarea id="c-subtasks" style="min-height:68px;"></textarea></div>
          <div class="form-field full">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <label for="c-body">Description / Notes</label>
              <button type="button" id="c-preview-toggle" class="copy-mini">Aperçu Markdown</button>
            </div>
            <textarea id="c-body"></textarea>
            <div id="c-body-preview" class="md-box hidden"></div>
          </div>
        </div>
      </div>
      <div id="c-tab-plan" class="tab-panel hidden">
        <div class="form-grid">
          <div class="form-field"><label for="c-horizon">Horizon</label><select id="c-horizon"></select></div>
          <div class="form-field"><label for="c-start">Date de début</label><input type="date" id="c-start"></div>
          <div class="form-field"><label for="c-due">Échéance</label><input type="date" id="c-due"></div>
          <div class="form-field"><label for="c-defer">En veille jusqu'au</label><input type="date" id="c-defer"></div>
        </div>
      </div>
      <div id="c-tab-links" class="tab-panel hidden">
        <div class="form-field">
          <label for="c-dep-search">Dépendances (attend les cartes cochées)</label>
          <input type="search" id="c-dep-search" placeholder="Filtrer par ID ou titre...">
          <div id="c-dep-picker" class="dep-picker-list"></div>
        </div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
        <button type="button" id="c-cancel">Annuler</button>
        <button type="submit" class="btn-primary">Créer la carte</button>
      </div>
    </form>
  </div>
</div>

<datalist id="dl-categories"></datalist>

<div id="settings-modal-backdrop" class="modal-backdrop hidden">
  <div class="modal" role="dialog" aria-modal="true">
    <div class="modal-head">
      <h2 style="margin:0;font-size:1.15rem;">Réglages du tableau</h2>
      <button type="button" id="s-close">Fermer</button>
    </div>
    <form id="s-labels-form" class="modal-section">
      <strong>1. Libellés du tableau, colonnes &amp; horizons</strong>
      <div class="form-grid">
        <div class="form-field full"><label for="s-title">Titre du tableau</label><input type="text" id="s-title" required></div>
        <div class="form-field"><label for="s-cat-label">Libellé du champ Catégorie</label><input type="text" id="s-cat-label" required></div>
        <div class="form-field"><label for="s-tag-label">Libellé du champ Tag</label><input type="text" id="s-tag-label" required></div>
      </div>
      <div id="s-columns-list" style="margin-top:10px;"></div>
      <div id="s-horizons-list" style="margin-top:10px;"></div>
      <div style="display:flex;justify-content:flex-end;margin-top:10px;">
        <button type="submit" class="btn-primary">Enregistrer les libellés</button>
      </div>
    </form>
    <div class="modal-section">
      <strong id="s-cats-heading">2. Gérer les catégories (renommage sur toutes les cartes)</strong>
      <div id="s-cats-list" style="margin-top:8px;"></div>
      <div class="tax-row" style="margin-top:8px;">
        <input type="text" id="s-new-cat" placeholder="Nouvelle catégorie...">
        <button type="button" id="s-add-cat">+ Ajouter</button>
      </div>
    </div>
    <div class="modal-section">
      <strong id="s-tags-heading">3. Gérer les tags (renommage sur toutes les cartes)</strong>
      <div id="s-tags-list" style="margin-top:8px;"></div>
      <div class="tax-row" style="margin-top:8px;">
        <input type="text" id="s-new-tag" placeholder="Nouveau tag...">
        <button type="button" id="s-add-tag">+ Ajouter</button>
      </div>
    </div>
  </div>
</div>
<div id="toast" class="toast hidden"></div>

<script id="kanban-data" type="application/json">__KANBAN_DATA__</script>
<script>
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("kanban-data").textContent);
  var FILTERS_KEY = "kanban_tasks_filters";
  var state = {
    view: "kanban",
    swimlanes: false,
    showStats: false,
    doneLimit: 5,
    search: "",
    priority: "",
    assignee: "",
    category: "",
    tag: "",
    flags: { blocked: false, waiting: false, overdue: false, stale: false, deferred: false, archived: false }
  };
  var draggedId = null;
  var activeModalId = null;
  var editDepsSelected = [];
  var createDepsSelected = [];

  function saveFilters() {
    try {
      localStorage.setItem(FILTERS_KEY, JSON.stringify({
        view: state.view,
        swimlanes: state.swimlanes,
        search: state.search,
        priority: state.priority,
        assignee: state.assignee,
        category: state.category,
        tag: state.tag,
        flags: state.flags
      }));
    } catch (e) {}
  }

  function loadFilters() {
    try {
      var raw = localStorage.getItem(FILTERS_KEY);
      if (!raw) return;
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return;
      if (parsed.view === "kanban" || parsed.view === "horizons" || parsed.view === "timeline") state.view = parsed.view;
      state.swimlanes = Boolean(parsed.swimlanes);
      state.search = typeof parsed.search === "string" ? parsed.search : "";
      state.priority = typeof parsed.priority === "string" ? parsed.priority : "";
      state.assignee = typeof parsed.assignee === "string" ? parsed.assignee : "";
      state.category = typeof parsed.category === "string" ? parsed.category : "";
      state.tag = typeof parsed.tag === "string" ? parsed.tag : "";
      if (parsed.flags && typeof parsed.flags === "object") {
        Object.keys(state.flags).forEach(function (k) {
          state.flags[k] = Boolean(parsed.flags[k]);
        });
      }
    } catch (e) {}
  }

  function syncFilterControlsFromState() {
    document.getElementById("f-search").value = state.search;
    document.querySelectorAll(".view-tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-view") === state.view);
    });
    var swBtn = document.getElementById("toggle-swimlanes");
    swBtn.textContent = "Swimlanes : " + (state.swimlanes ? "Oui" : "Non");
    swBtn.classList.toggle("active", state.swimlanes);
    document.querySelectorAll(".chip-btn").forEach(function (b) {
      var f = b.getAttribute("data-flag");
      b.classList.toggle("active", Boolean(state.flags[f]));
    });
  }

  function formatElapsed(hours) {
    var h = Number(hours || 0);
    if (h < 1) return "< 1 h";
    if (h < 48) return Math.floor(h) + " h";
    return Math.floor(h / 24) + " j";
  }

  function appendInlineMd(parent, text) {
    var re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (tok.slice(0, 2) === "**") {
        parent.appendChild(el("strong", "", tok.slice(2, -2)));
      } else {
        parent.appendChild(el("code", "", tok.slice(1, -1)));
      }
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderMarkdownSafe(container, rawText) {
    container.replaceChildren();
    var text = String(rawText || "").trim();
    if (!text) {
      container.appendChild(el("div", "empty-msg", "(Aucune note)"));
      return;
    }
    var lines = text.split(/\r?\n/);
    var ul = null;
    var inCode = false;
    var codeLines = [];
    function flushList() {
      if (ul) { container.appendChild(ul); ul = null; }
    }
    lines.forEach(function (line) {
      if (line.trim().slice(0, 3) === "```") {
        flushList();
        if (!inCode) {
          inCode = true;
          codeLines = [];
        } else {
          var pre = document.createElement("pre");
          pre.appendChild(el("code", "", codeLines.join("\n")));
          container.appendChild(pre);
          inCode = false;
        }
        return;
      }
      if (inCode) { codeLines.push(line); return; }
      if (!line.trim()) { flushList(); return; }
      var hMatch = line.match(/^(#{1,3})\s+(.*)$/);
      if (hMatch) {
        flushList();
        var tagName = "h" + hMatch[1].length;
        var hNode = document.createElement(tagName);
        appendInlineMd(hNode, hMatch[2]);
        container.appendChild(hNode);
        return;
      }
      var liMatch = line.match(/^\s*[-*]\s+(.*)$/);
      if (liMatch) {
        if (!ul) ul = document.createElement("ul");
        var li = document.createElement("li");
        appendInlineMd(li, liMatch[1]);
        ul.appendChild(li);
        return;
      }
      flushList();
      var p = document.createElement("p");
      appendInlineMd(p, line);
      container.appendChild(p);
    });
    flushList();
    if (inCode && codeLines.length) {
      var preEnd = document.createElement("pre");
      preEnd.appendChild(el("code", "", codeLines.join("\n")));
      container.appendChild(preEnd);
    }
  }

  function findTaskById(tid) {
    for (var i = 0; i < DATA.tasks.length; i++) {
      if (DATA.tasks[i].id === tid) return DATA.tasks[i];
    }
    return null;
  }

  function applyInteractiveUi() {
    var live = Boolean(DATA.interactive);
    var badge = document.getElementById("mode-badge");
    badge.textContent = live ? "Mode interactif" : "Lecture seule";
    badge.classList.toggle("live", live);
    document.getElementById("btn-new-card").classList.toggle("hidden", !live);
    document.getElementById("btn-settings").classList.toggle("hidden", !live);
    document.getElementById("btn-refresh").classList.toggle("hidden", !live);
    applyLabelUi();
  }

  function applyLabelUi() {
    var lbl = DATA.labels || { category: "Catégorie", tag: "Tag" };
    var catLbl = lbl.category || "Catégorie";
    var tagLbl = lbl.tag || "Tag";
    document.getElementById("board-title").textContent = DATA.title;
    document.getElementById("lbl-e-category").textContent = catLbl;
    document.getElementById("lbl-c-category").textContent = catLbl;
    document.getElementById("lbl-e-tags").textContent = tagLbl + " (séparés par virgule)";
    document.getElementById("lbl-c-tags").textContent = tagLbl + " (séparés par virgule)";
    var dl = document.getElementById("dl-categories");
    dl.replaceChildren();
    (DATA.categories || []).forEach(function (c) {
      var opt = document.createElement("option");
      opt.value = c;
      dl.appendChild(opt);
    });
  }

  function runApiAction(payload, onSuccess, onError) {
    if (!DATA.interactive) {
      showToast("Mode lecture seule : lancez 'kanban.py serve' pour éditer ici.");
      return;
    }
    fetch("/api/action", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Kanban-Token": DATA.csrf_token || ""
      },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().then(function (body) { return { ok: res.ok, body: body }; });
    }).then(function (resp) {
      if (!resp.ok || !resp.body.ok) {
        var errMsg = resp.body.error || "action refusée";
        showToast("Erreur : " + errMsg);
        if (onError) onError(errMsg);
        return;
      }
      DATA = resp.body.state;
      populateSelects();
      renderMain();
      if (resp.body.result && resp.body.result.wip_warning) {
        showToast(resp.body.result.wip_warning);
      }
      if (onSuccess) onSuccess(resp.body.result);
    }).catch(function () {
      showToast("Serveur local inaccessible.");
    });
  }

  function refreshFromServer() {
    if (!DATA.interactive) return;
    fetch("/api/state").then(function (res) { return res.json(); }).then(function (fresh) {
      DATA = fresh;
      populateSelects();
      renderMain();
      showToast("Tableau actualisé");
    }).catch(function () {
      showToast("Impossible de joindre le serveur local.");
    });
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function showToast(msg) {
    var toast = document.getElementById("toast");
    toast.textContent = msg;
    toast.classList.remove("hidden");
    setTimeout(function () { toast.classList.add("hidden"); }, 1800);
  }

  function copyOrder(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        showToast("Copié : " + text);
      }).catch(function () { fallbackCopy(text); });
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); showToast("Copié : " + text); } catch (e) {}
    document.body.removeChild(ta);
  }

  function initTheme() {
    try {
      var saved = localStorage.getItem("kanban_tasks_theme");
      if (saved === "dark") document.documentElement.setAttribute("data-theme", "dark");
    } catch (e) {}
  }

  function toggleTheme() {
    var isDark = document.documentElement.getAttribute("data-theme") === "dark";
    var next = isDark ? "light" : "dark";
    if (next === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("kanban_tasks_theme", next); } catch (e) {}
  }

  function populateSelects() {
    var prios = ["urgente", "haute", "normale", "basse"];
    var assignees = {};
    var cats = {};
    var tags = {};
    (DATA.categories || []).forEach(function (c) { if (c) cats[c] = true; });
    (DATA.tags || []).forEach(function (tg) { if (tg) tags[tg] = true; });
    DATA.tasks.forEach(function (t) {
      if (t.assignee) assignees[t.assignee] = true;
      if (t.category) cats[t.category] = true;
      (t.tags || []).forEach(function (tg) { tags[tg] = true; });
    });
    var lbl = DATA.labels || { category: "Catégorie", tag: "Tag" };
    fillSelect("f-priority", prios, "Priorité : Toutes", state.priority);
    fillSelect("f-assignee", Object.keys(assignees).sort(), "Assignation : Tous", state.assignee);
    fillSelect("f-category", Object.keys(cats).sort(), (lbl.category || "Catégorie") + " : Toutes", state.category);
    fillSelect("f-tag", Object.keys(tags).sort(), (lbl.tag || "Tag") + " : Tous", state.tag);
  }

  function fillSelect(id, values, firstLabel, currentVal) {
    var sel = document.getElementById(id);
    sel.replaceChildren();
    sel.appendChild(el("option", "", firstLabel));
    sel.firstChild.value = "";
    values.forEach(function (v) {
      var opt = el("option", "", v);
      opt.value = v;
      sel.appendChild(opt);
    });
    if (currentVal && values.indexOf(currentVal) === -1) {
      var ghost = el("option", "", currentVal + " (0)");
      ghost.value = currentVal;
      sel.appendChild(ghost);
    }
    if (currentVal) sel.value = currentVal;
  }

  function filteredTasks() {
    var q = state.search.trim().toLowerCase();
    return DATA.tasks.filter(function (t) {
      if (!state.flags.archived && t.archived) return false;
      if (state.flags.archived && !t.archived) return false;
      if (state.priority && t.priority !== state.priority) return false;
      if (state.assignee && t.assignee !== state.assignee) return false;
      if (state.category && t.category !== state.category) return false;
      if (state.tag && (t.tags || []).indexOf(state.tag) === -1) return false;
      if (state.flags.blocked && !t.blocked) return false;
      if (state.flags.waiting && !t.implicitly_blocked) return false;
      if (state.flags.overdue && !t.is_overdue) return false;
      if (state.flags.stale && !t.is_stale) return false;
      if (state.flags.deferred && !t.is_deferred) return false;
      if (q) {
        var hay = [t.id, t.title, t.category, t.assignee, t.body, (t.tags || []).join(" ")].join(" ").toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function buildCard(t, showStatusBadge) {
    var cls = "card prio-" + t.priority;
    if (t.role === "done" || t.archived) cls += " is-done";
    if (t.blocked) cls += " is-blocked";
    var card = el("article", cls);
    var top = el("div", "card-top");
    top.appendChild(el("span", "card-id", t.id));
    var copyBtn = el("button", "copy-mini", "Copier ordre");
    copyBtn.type = "button";
    copyBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var cmd = t.role === "review" ? ("Valide " + t.id + " en terminé") : ("Passe " + t.id + " en cours");
      copyOrder(cmd);
    });
    top.appendChild(copyBtn);
    card.appendChild(top);
    var cardTitle = el("div", "card-title", t.title);
    cardTitle.setAttribute("title", t.title);
    card.appendChild(cardTitle);

    if (t.blocked) {
      card.appendChild(el("div", "card-context", "Bloqué : " + (t.blocked_reason || "motif non précisé")));
    }

    var badges = el("div", "badges");
    if (showStatusBadge) badges.appendChild(el("span", "badge accent", t.status_label));
    if (t.priority !== "normale") {
      var pCls = t.priority === "urgente" ? "danger" : (t.priority === "haute" ? "warn" : "");
      badges.appendChild(el("span", "badge " + pCls, t.priority));
    }
    if (t.subtasks_total > 0) {
      var sCls = t.subtasks_done === t.subtasks_total ? "ok" : "";
      badges.appendChild(el("span", "badge " + sCls, t.subtasks_done + "/" + t.subtasks_total));
    }
    if (t.assignee) badges.appendChild(el("span", "badge", "@" + t.assignee));
    if (t.category) badges.appendChild(el("span", "badge purple", t.category));
    if (t.horizon) {
      var hLabel = DATA.horizon_labels[t.horizon] || t.horizon;
      badges.appendChild(el("span", "badge", hLabel));
    }
    if (t.due_date) {
      badges.appendChild(el("span", "badge " + (t.is_overdue ? "danger" : ""), (t.is_overdue ? "Retard " : "Éch. ") + t.due_date));
    }
    if ((t.role === "in_progress" || t.role === "review") && !t.archived) {
      var durTxt = formatElapsed(t.age_in_status_hours);
      badges.appendChild(el("span", "badge " + (t.is_stale ? "warn" : ""), (t.is_stale ? "Stagnante " : "Depuis ") + durTxt));
    }
    if (t.attachments && t.attachments.length > 0) {
      badges.appendChild(el("span", "badge", "Pièces: " + t.attachments.length));
    }
    if (t.is_deferred) badges.appendChild(el("span", "badge", "Veille → " + t.defer_until));
    (t.tags || []).forEach(function (tg) { badges.appendChild(el("span", "badge", "#" + tg)); });

    var depDrawer = el("div", "dep-drawer hidden");
    var openDrawerKind = "";
    function toggleDepDrawer(kind, ids, headerText, btnNode, otherBtnNode) {
      if (openDrawerKind === kind) {
        openDrawerKind = "";
        depDrawer.classList.add("hidden");
        btnNode.setAttribute("aria-expanded", "false");
        return;
      }
      openDrawerKind = kind;
      btnNode.setAttribute("aria-expanded", "true");
      if (otherBtnNode) otherBtnNode.setAttribute("aria-expanded", "false");
      depDrawer.replaceChildren();
      depDrawer.appendChild(el("div", "", headerText));
      var ul = document.createElement("ul");
      ids.forEach(function (lid) {
        var li = document.createElement("li");
        var linked = findTaskById(lid);
        if (linked) {
          var linkBtn = el("button", "dep-link", linked.id + " · " + linked.title + " (" + linked.status_label + ")");
          linkBtn.type = "button";
          linkBtn.addEventListener("click", function (ev) {
            ev.stopPropagation();
            openModal(linked);
          });
          li.appendChild(linkBtn);
        } else {
          li.textContent = lid;
        }
        ul.appendChild(li);
      });
      depDrawer.appendChild(ul);
      depDrawer.classList.remove("hidden");
    }

    var waitBtn = null;
    var unblockBtn = null;
    if (t.unmet_dependencies && t.unmet_dependencies.length > 0) {
      waitBtn = el("button", "dep-btn warn", "Attend " + t.unmet_dependencies.length);
      waitBtn.type = "button";
      waitBtn.setAttribute("aria-expanded", "false");
      badges.appendChild(waitBtn);
    }
    if (t.unblocks && t.unblocks.length > 0) {
      unblockBtn = el("button", "dep-btn accent", "Débloque " + t.unblocks.length);
      unblockBtn.type = "button";
      unblockBtn.setAttribute("aria-expanded", "false");
      badges.appendChild(unblockBtn);
    }
    if (waitBtn) {
      waitBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        toggleDepDrawer("wait", t.unmet_dependencies, "Attend les tâches :", waitBtn, unblockBtn);
      });
    }
    if (unblockBtn) {
      unblockBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        toggleDepDrawer("unblock", t.unblocks, "Débloque les tâches :", unblockBtn, waitBtn);
      });
    }

    card.appendChild(badges);
    if (waitBtn || unblockBtn) card.appendChild(depDrawer);

    if (DATA.interactive && !t.archived) {
      card.draggable = true;
      card.addEventListener("dragstart", function (ev) {
        draggedId = t.id;
        card.classList.add("dragging");
        if (ev.dataTransfer) {
          ev.dataTransfer.setData("text/plain", t.id);
          ev.dataTransfer.effectAllowed = "move";
        }
      });
      card.addEventListener("dragend", function () {
        draggedId = null;
        card.classList.remove("dragging");
        document.querySelectorAll(".column.drag-over").forEach(function (c) { c.classList.remove("drag-over"); });
      });
    }

    card.addEventListener("click", function () { openModal(t); });
    return card;
  }

  function renderColumns(container, tasks, columns, showStatusBadge, matchFn, dropKind) {
    var grid = el("div", "board-grid");
    columns.forEach(function (col) {
      var colNode = el("section", "column");
      var colTasks = tasks.filter(function (t) { return matchFn(t, col); });
      if (col.role === "done") {
        colTasks.sort(function (a, b) {
          var da = a.entered_status_at || "";
          var db = b.entered_status_at || "";
          return db.localeCompare(da);
        });
      }
      var head = el("div", "col-header");
      head.appendChild(el("span", "", col.label));
      var wipTxt = col.wip_limit ? (colTasks.length + " / " + col.wip_limit) : String(colTasks.length);
      var over = col.wip_limit && colTasks.length > col.wip_limit;
      head.appendChild(el("span", "wip-badge" + (over ? " over" : ""), wipTxt));
      colNode.appendChild(head);

      var visibleTasks = colTasks;
      var remainingDone = 0;
      if (col.role === "done" && colTasks.length > state.doneLimit) {
        visibleTasks = colTasks.slice(0, state.doneLimit);
        remainingDone = colTasks.length - state.doneLimit;
      }

      if (visibleTasks.length === 0) {
        colNode.appendChild(el("div", "empty-msg", "Aucune carte"));
      } else {
        visibleTasks.forEach(function (t) { colNode.appendChild(buildCard(t, showStatusBadge)); });
      }
      if (remainingDone > 0) {
        var moreBtn = el("button", "col-more-btn", "Afficher " + remainingDone + " carte(s) de plus");
        moreBtn.type = "button";
        moreBtn.addEventListener("click", function () {
          state.doneLimit += 10;
          renderMain();
        });
        colNode.appendChild(moreBtn);
      }
      if (DATA.interactive && dropKind && col.role !== "unknown") {
        colNode.addEventListener("dragover", function (ev) {
          ev.preventDefault();
          colNode.classList.add("drag-over");
        });
        colNode.addEventListener("dragleave", function (ev) {
          if (!colNode.contains(ev.relatedTarget)) colNode.classList.remove("drag-over");
        });
        colNode.addEventListener("drop", function (ev) {
          ev.preventDefault();
          colNode.classList.remove("drag-over");
          var tid = (ev.dataTransfer && ev.dataTransfer.getData("text/plain")) || draggedId;
          if (!tid) return;
          var cardObj = findTaskById(tid);
          if (!cardObj || cardObj.archived) return;
          if (dropKind === "status") {
            if (cardObj.status === col.id) return;
            runApiAction({ op: "move", id: tid, status: col.id, actor: "humain" }, function (res) {
              if (!res || !res.wip_warning) showToast(tid + " → " + col.label);
            });
          } else if (dropKind === "horizon") {
            if ((cardObj.horizon || "") === col.id) return;
            runApiAction({ op: "edit", id: tid, horizon: col.id }, function () {
              showToast(tid + " → " + col.label);
            });
          }
        });
      }
      grid.appendChild(colNode);
    });
    container.appendChild(grid);
  }

  function renderTimeline(container, tasks) {
    var dated = tasks.filter(function (t) { return t.start_date || t.due_date; });
    var undated = tasks.filter(function (t) { return !t.start_date && !t.due_date; });
    var layout = el("div", "timeline-layout");
    var left = el("div", "timeline-box");
    left.appendChild(el("h3", "", "Frise chronologique (" + dated.length + " carte(s) datée(s))"));

    if (dated.length === 0) {
      left.appendChild(el("div", "empty-msg", "Ajoutez start_date ou due_date à vos cartes pour les visualiser sur la frise."));
    } else {
      var todayMs = Date.parse(DATA.today + "T00:00:00Z");
      var minMs = todayMs - 7 * 86400000;
      var maxMs = todayMs + 21 * 86400000;
      var ranges = dated.map(function (t) {
        var s = Date.parse((t.start_date || t.due_date) + "T00:00:00Z");
        var e = Date.parse((t.due_date || t.start_date) + "T00:00:00Z");
        if (e < s) { var tmp = s; s = e; e = tmp; }
        if (s < minMs) minMs = s;
        if (e > maxMs) maxMs = e;
        return { task: t, start: s, end: e + 86400000 };
      });
      var span = Math.max(86400000, maxMs - minMs);

      var weekStrip = el("div", "week-strip");
      for (var w = minMs; w < maxMs; w += 7 * 86400000) {
        var wEnd = w + 7 * 86400000;
        var load = ranges.filter(function (r) { return r.start < wEnd && r.end > w && r.task.role !== "done"; }).length;
        var dLabel = new Date(w).toISOString().slice(0, 10);
        weekStrip.appendChild(el("span", "badge" + (load >= 4 ? " warn" : ""), "Sem. " + dLabel + " : " + load + " active(s)"));
      }
      left.appendChild(weekStrip);

      ranges.forEach(function (r) {
        var row = el("div", "tl-row");
        var label = el("div", "", r.task.id + " · " + r.task.title);
        label.style.fontSize = "0.84rem";
        var track = el("div", "tl-track");
        var todayPct = ((todayMs - minMs) / span) * 100;
        if (todayPct >= 0 && todayPct <= 100) {
          var marker = el("div", "tl-today");
          marker.style.left = todayPct + "%";
          track.appendChild(marker);
        }
        var leftPct = ((r.start - minMs) / span) * 100;
        var widthPct = Math.max(2.5, ((r.end - r.start) / span) * 100);
        var barCls = "tl-bar" + (r.task.is_overdue ? " overdue" : (r.task.role === "done" ? " done" : ""));
        var bar = el("div", barCls, (r.task.start_date || r.task.due_date) + " → " + (r.task.due_date || r.task.start_date));
        bar.style.left = leftPct + "%";
        bar.style.width = Math.min(100 - leftPct, widthPct) + "%";
        bar.addEventListener("click", function () { openModal(r.task); });
        track.appendChild(bar);
        row.appendChild(label);
        row.appendChild(track);
        left.appendChild(row);
      });
    }

    var right = el("div", "timeline-box");
    right.appendChild(el("h3", "", "Sans date fixe (" + undated.length + ")"));
    if (undated.length === 0) {
      right.appendChild(el("div", "empty-msg", "Toutes les cartes filtrées sont datées."));
    } else {
      undated.forEach(function (t) { right.appendChild(buildCard(t, true)); });
    }
    layout.appendChild(left);
    layout.appendChild(right);
    container.appendChild(layout);
  }

  function renderStats() {
    var panel = document.getElementById("stats-panel");
    panel.replaceChildren();
    panel.classList.toggle("hidden", !state.showStats);
    if (!state.showStats) return;
    var s = DATA.stats;
    var items = [
      ["Cartes actives", s.active_count],
      ["Archivées", s.archived_count],
      ["Bloquées / En attente", s.blocked_count + " / " + s.implicitly_blocked_count],
      ["En retard / Stagnantes", s.overdue_count + " / " + s.stale_count],
      ["Cycle time moyen", s.avg_cycle_time_hours !== null ? (s.avg_cycle_time_hours + " h") : "—"],
      ["Cycle time médian", s.median_cycle_time_hours !== null ? (s.median_cycle_time_hours + " h") : "—"],
      ["Lead time moyen", s.avg_lead_time_hours !== null ? (s.avg_lead_time_hours + " h") : "—"],
      ["Âge max en colonne", s.max_work_item_age_days + " j"],
      ["Retours (rework)", s.rework_total]
    ];
    items.forEach(function (pair) {
      var box = el("div", "stat-box");
      box.appendChild(el("div", "k", pair[0]));
      box.appendChild(el("div", "v", pair[1]));
      panel.appendChild(box);
    });
  }

  function renderMain() {
    var main = document.getElementById("app-main");
    main.replaceChildren();
    var tasks = filteredTasks();
    document.getElementById("board-subtitle").textContent =
      tasks.length + " carte(s) affichée(s) · Généré le " + DATA.generated_at + " · Aujourd'hui : " + DATA.today;

    if (state.view === "kanban") {
      var knownColIds = DATA.columns.map(function (c) { return c.id; });
      var hasUnknown = tasks.some(function (t) { return !t.archived && knownColIds.indexOf(t.status) === -1; });
      var colsToRender = hasUnknown
        ? DATA.columns.concat([{ id: "__unknown__", label: "Statut inconnu (à vérifier)", wip_limit: null, role: "unknown" }])
        : DATA.columns;
      var colMatchFn = function (t, col) {
        if (col.id === "__unknown__") return knownColIds.indexOf(t.status) === -1;
        return t.status === col.id;
      };
      if (!state.swimlanes) {
        renderColumns(main, tasks, colsToRender, false, colMatchFn, "status");
      } else {
        var groups = {};
        tasks.forEach(function (t) {
          var key = t.category || "Sans catégorie";
          if (!groups[key]) groups[key] = [];
          groups[key].push(t);
        });
        Object.keys(groups).sort().forEach(function (cat) {
          var lane = el("div", "swimlane");
          lane.appendChild(el("div", "swimlane-title", cat + " (" + groups[cat].length + ")"));
          renderColumns(lane, groups[cat], colsToRender, false, colMatchFn, "status");
          main.appendChild(lane);
        });
      }
    } else if (state.view === "horizons") {
      var hCols = DATA.horizons.map(function (h) { return { id: h.id, label: h.label, wip_limit: null }; });
      hCols.push({ id: "", label: "Sans horizon", wip_limit: null });
      renderColumns(main, tasks, hCols, true, function (t, col) { return (t.horizon || "") === col.id; }, "horizon");
    } else {
      renderTimeline(main, tasks);
    }
    renderStats();
    applyInteractiveUi();
  }

  function populateFormSelects(statusSel, prioSel, horizonSel, currentStatus, currentPrio, currentHorizon) {
    statusSel.replaceChildren();
    DATA.columns.forEach(function (c) {
      var opt = el("option", "", c.label);
      opt.value = c.id;
      statusSel.appendChild(opt);
    });
    statusSel.value = currentStatus || DATA.columns[0].id;

    prioSel.replaceChildren();
    ["basse", "normale", "haute", "urgente"].forEach(function (p) {
      var opt = el("option", "", p);
      opt.value = p;
      prioSel.appendChild(opt);
    });
    prioSel.value = currentPrio || "normale";

    horizonSel.replaceChildren();
    var emptyOpt = el("option", "", "(Sans horizon)");
    emptyOpt.value = "";
    horizonSel.appendChild(emptyOpt);
    DATA.horizons.forEach(function (h) {
      var opt = el("option", "", h.label);
      opt.value = h.id;
      horizonSel.appendChild(opt);
    });
    horizonSel.value = currentHorizon || "";
  }

  function switchFormTabs(tabsContainerId, activeTabId) {
    var tabsRoot = document.getElementById(tabsContainerId);
    tabsRoot.querySelectorAll(".modal-tab").forEach(function (btn) {
      var target = btn.getAttribute("data-tab");
      var isAct = target === activeTabId;
      btn.classList.toggle("active", isAct);
      var panel = document.getElementById(target);
      if (panel) panel.classList.toggle("hidden", !isAct);
    });
  }

  function updateEditTabBadges(t) {
    var planCnt = 0;
    if (document.getElementById("e-horizon").value) planCnt++;
    if (document.getElementById("e-start").value) planCnt++;
    if (document.getElementById("e-due").value) planCnt++;
    if (document.getElementById("e-defer").value) planCnt++;
    document.getElementById("e-tab-plan-btn").textContent = "Planning (" + planCnt + ")";
    var attLen = t && t.attachments ? t.attachments.length : 0;
    document.getElementById("e-tab-links-btn").textContent = "Dépendances & Pièces (" + (editDepsSelected.length + attLen) + ")";
  }

  function updateCreateTabBadges() {
    var planCnt = 0;
    if (document.getElementById("c-horizon").value) planCnt++;
    if (document.getElementById("c-start").value) planCnt++;
    if (document.getElementById("c-due").value) planCnt++;
    if (document.getElementById("c-defer").value) planCnt++;
    document.getElementById("c-tab-plan-btn").textContent = "Planning (" + planCnt + ")";
    document.getElementById("c-tab-links-btn").textContent = "Dépendances (" + createDepsSelected.length + ")";
  }

  function renderDepPicker(pickerId, searchId, excludeId, selectedArr, onChange) {
    var box = document.getElementById(pickerId);
    var q = (document.getElementById(searchId).value || "").trim().toLowerCase();
    box.replaceChildren();
    var candidates = DATA.tasks.filter(function (item) {
      if (item.archived || item.id === excludeId) return false;
      if (q && (item.id + " " + item.title + " " + item.status_label).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    if (candidates.length === 0) {
      box.appendChild(el("div", "empty-msg", "Aucune carte correspondante."));
      return;
    }
    candidates.forEach(function (item) {
      var row = el("label", "dep-picker-item");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedArr.indexOf(item.id) !== -1;
      cb.addEventListener("change", function () {
        var idx = selectedArr.indexOf(item.id);
        if (cb.checked && idx === -1) selectedArr.push(item.id);
        else if (!cb.checked && idx !== -1) selectedArr.splice(idx, 1);
        if (onChange) onChange();
      });
      row.appendChild(cb);
      row.appendChild(el("span", "", item.id + " · " + item.title + " (" + item.status_label + ")"));
      box.appendChild(row);
    });
  }

  function renderAttachmentsList(containerId, t, canEdit) {
    var box = document.getElementById(containerId);
    box.replaceChildren();
    var atts = t.attachments || [];
    if (atts.length === 0) {
      box.appendChild(el("div", "empty-msg", "Aucune pièce jointe."));
      return;
    }
    atts.forEach(function (att) {
      var row = el("div", "att-row");
      var isManaged = att.indexOf("attachments/") === 0 && Boolean(DATA.interactive);
      if (isManaged) {
        var link = document.createElement("a");
        link.href = "/" + att;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = att.split("/").pop();
        row.appendChild(link);
      } else {
        row.appendChild(el("span", "", att));
      }
      if (canEdit) {
        var rmBtn = el("button", "", "Retirer");
        rmBtn.type = "button";
        rmBtn.addEventListener("click", function () {
          runApiAction({ op: "detach", id: t.id, target: att }, function () {
            var updated = findTaskById(t.id);
            if (updated) {
              renderAttachmentsList("m-atts-list", updated, true);
              renderAttachmentsList("e-atts-list", updated, true);
              updateEditTabBadges(updated);
            }
            showToast("Pièce retirée");
          });
        });
        row.appendChild(rmBtn);
      }
      box.appendChild(row);
    });
  }

  function setModalEditMode(isEditing) {
    document.getElementById("m-view-section").classList.toggle("hidden", isEditing);
    document.getElementById("m-edit-section").classList.toggle("hidden", !isEditing);
    if (!isEditing) {
      document.getElementById("e-body-preview").classList.add("hidden");
      document.getElementById("e-body").classList.remove("hidden");
      document.getElementById("e-preview-toggle").textContent = "Aperçu Markdown";
    }
  }

  function openModal(t) {
    activeModalId = t.id;
    setModalEditMode(false);
    document.getElementById("m-delete-bar").classList.add("hidden");
    document.getElementById("btn-delete-force").classList.add("hidden");
    document.getElementById("m-delete-msg").textContent = "Confirmer la suppression définitive de cette carte ?";

    document.getElementById("m-id").textContent = t.id + " · " + t.status_label;
    document.getElementById("m-title").textContent = t.title;

    var canEdit = Boolean(DATA.interactive) && !t.archived;
    document.getElementById("btn-edit-card").classList.toggle("hidden", !canEdit);
    document.getElementById("btn-archive-card").classList.toggle("hidden", !(canEdit && t.role === "done"));
    document.getElementById("btn-delete-card").classList.toggle("hidden", !canEdit);

    var badges = document.getElementById("m-badges");
    badges.replaceChildren();
    var lbl = DATA.labels || { category: "Catégorie", tag: "Tag" };
    badges.appendChild(el("span", "badge", "Priorité : " + t.priority));
    if (t.assignee) badges.appendChild(el("span", "badge", "Assigné : " + t.assignee));
    if (t.category) badges.appendChild(el("span", "badge purple", (lbl.category || "Catégorie") + " : " + t.category));
    if (t.horizon) badges.appendChild(el("span", "badge", "Horizon : " + (DATA.horizon_labels[t.horizon] || t.horizon)));
    if (t.start_date) badges.appendChild(el("span", "badge", "Début : " + t.start_date));
    if (t.due_date) badges.appendChild(el("span", "badge " + (t.is_overdue ? "danger" : ""), "Échéance : " + t.due_date));
    if (t.defer_until) badges.appendChild(el("span", "badge", "En veille jusqu'au : " + t.defer_until));
    if (t.blocked) badges.appendChild(el("span", "badge danger", "Bloqué : " + (t.blocked_reason || "sans motif")));
    if (t.implicitly_blocked) badges.appendChild(el("span", "badge warn", "Attend : " + t.unmet_dependencies.join(", ")));

    var depsSec = document.getElementById("m-deps-section");
    var depsList = document.getElementById("m-deps-list");
    depsList.replaceChildren();
    var hasDeps = (t.depends_on && t.depends_on.length > 0) || (t.unblocks && t.unblocks.length > 0);
    depsSec.classList.toggle("hidden", !hasDeps);
    if (t.depends_on && t.depends_on.length > 0) {
      var pWait = el("div", "", "Dépend de : ");
      t.depends_on.forEach(function (did, i) {
        if (i > 0) pWait.appendChild(document.createTextNode(", "));
        var linked = findTaskById(did);
        if (linked) {
          var b = el("button", "dep-link", linked.id + " (" + linked.status_label + ")");
          b.type = "button";
          b.addEventListener("click", function () { openModal(linked); });
          pWait.appendChild(b);
        } else {
          pWait.appendChild(document.createTextNode(did));
        }
      });
      depsList.appendChild(pWait);
    }
    if (t.unblocks && t.unblocks.length > 0) {
      var pUnb = el("div", "", "Débloque : ");
      t.unblocks.forEach(function (uid, i) {
        if (i > 0) pUnb.appendChild(document.createTextNode(", "));
        var linked = findTaskById(uid);
        if (linked) {
          var b = el("button", "dep-link", linked.id + " (" + linked.status_label + ")");
          b.type = "button";
          b.addEventListener("click", function () { openModal(linked); });
          pUnb.appendChild(b);
        } else {
          pUnb.appendChild(document.createTextNode(uid));
        }
      });
      depsList.appendChild(pUnb);
    }

    var subWrap = document.getElementById("m-subtasks-wrap");
    var subList = document.getElementById("m-subtasks");
    subList.replaceChildren();
    var subs = t.subtasks || [];
    subWrap.classList.toggle("hidden", subs.length === 0);
    subs.forEach(function (st) {
      var row = el("label", "subtask-row" + (st.done ? " done" : ""));
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = Boolean(st.done);
      cb.disabled = !canEdit;
      if (canEdit) {
        cb.addEventListener("change", function () {
          var opName = cb.checked ? "check" : "uncheck";
          runApiAction({ op: opName, id: t.id, target: String(st.index) }, function () {
            var updated = findTaskById(t.id);
            if (updated) openModal(updated);
          });
        });
      }
      row.appendChild(cb);
      row.appendChild(el("span", "", st.text));
      subList.appendChild(row);
    });

    var attsWrap = document.getElementById("m-atts-wrap");
    var hasAtts = t.attachments && t.attachments.length > 0;
    attsWrap.classList.toggle("hidden", !hasAtts);
    if (hasAtts) renderAttachmentsList("m-atts-list", t, canEdit);

    var orders = document.getElementById("m-orders");
    orders.replaceChildren();
    var suggested = [
      "Passe " + t.id + " en cours",
      "Passe " + t.id + " en revue",
      "Valide " + t.id + " en terminé",
      t.blocked ? ("Débloque " + t.id) : ("Bloque " + t.id + " : préciser le motif")
    ];
    suggested.forEach(function (cmd) {
      var btn = el("button", "", cmd);
      btn.type = "button";
      btn.addEventListener("click", function () { copyOrder(cmd); });
      orders.appendChild(btn);
    });

    renderMarkdownSafe(document.getElementById("m-body"), t.body);
    var hist = document.getElementById("m-history");
    hist.replaceChildren();
    (t.history || []).forEach(function (h) { hist.appendChild(el("li", "", h)); });
    document.getElementById("modal-backdrop").classList.remove("hidden");
  }

  function closeModal() {
    activeModalId = null;
    setModalEditMode(false);
    document.getElementById("modal-backdrop").classList.add("hidden");
  }

  function openCreateModal() {
    var defaultCol = findTaskById ? "a_faire" : DATA.columns[0].id;
    var hasTodo = DATA.columns.some(function (c) { return c.id === "a_faire"; });
    populateFormSelects(
      document.getElementById("c-status"),
      document.getElementById("c-priority"),
      document.getElementById("c-horizon"),
      hasTodo ? defaultCol : DATA.columns[0].id,
      "normale",
      ""
    );
    document.getElementById("c-title").value = "";
    document.getElementById("c-category").value = "";
    document.getElementById("c-assignee").value = "";
    document.getElementById("c-tags").value = "";
    document.getElementById("c-start").value = "";
    document.getElementById("c-due").value = "";
    document.getElementById("c-defer").value = "";
    document.getElementById("c-subtasks").value = "";
    document.getElementById("c-body").value = "";
    document.getElementById("c-body-preview").classList.add("hidden");
    document.getElementById("c-body").classList.remove("hidden");
    document.getElementById("c-preview-toggle").textContent = "Aperçu Markdown";
    createDepsSelected = [];
    document.getElementById("c-dep-search").value = "";
    renderDepPicker("c-dep-picker", "c-dep-search", "", createDepsSelected, updateCreateTabBadges);
    updateCreateTabBadges();
    switchFormTabs("c-tabs", "c-tab-general");
    document.getElementById("create-modal-backdrop").classList.remove("hidden");
    document.getElementById("c-title").focus();
  }

  function closeCreateModal() {
    document.getElementById("create-modal-backdrop").classList.add("hidden");
  }

  function renderTaxonomyEditor(containerId, items, kind) {
    var box = document.getElementById(containerId);
    box.replaceChildren();
    if (!items || items.length === 0) {
      box.appendChild(el("div", "empty-msg", "Aucun élément pour l'instant."));
      return;
    }
    items.forEach(function (val) {
      var row = el("div", "tax-row");
      var inp = document.createElement("input");
      inp.type = "text";
      inp.value = val;
      var btnRename = el("button", "", "Renommer");
      btnRename.type = "button";
      btnRename.addEventListener("click", function () {
        var nextVal = inp.value.trim();
        if (!nextVal || nextVal === val) return;
        var opName = kind === "category" ? "rename_category" : "rename_tag";
        runApiAction({ op: opName, old: val, new: nextVal }, function (res) {
          if (kind === "category" && state.category === val) state.category = nextVal;
          if (kind === "tag" && state.tag === val) state.tag = nextVal;
          openSettingsModal();
          showToast(val + " → " + nextVal + " (" + (res ? res.updated_cards : 0) + " carte(s))");
        });
      });
      var btnRemove = el("button", "", "Retirer");
      btnRemove.type = "button";
      btnRemove.addEventListener("click", function () {
        var opName = kind === "category" ? "rename_category" : "rename_tag";
        runApiAction({ op: opName, old: val, new: "" }, function (res) {
          if (kind === "category" && state.category === val) state.category = "";
          if (kind === "tag" && state.tag === val) state.tag = "";
          openSettingsModal();
          showToast(val + " retiré (" + (res ? res.updated_cards : 0) + " carte(s))");
        });
      });
      row.appendChild(inp);
      row.appendChild(btnRename);
      row.appendChild(btnRemove);
      box.appendChild(row);
    });
  }

  function openSettingsModal() {
    var lbl = DATA.labels || { category: "Catégorie", tag: "Tag" };
    document.getElementById("s-title").value = DATA.title || "";
    document.getElementById("s-cat-label").value = lbl.category || "Catégorie";
    document.getElementById("s-tag-label").value = lbl.tag || "Tag";

    var colBox = document.getElementById("s-columns-list");
    colBox.replaceChildren();
    colBox.appendChild(el("div", "empty-msg", "Colonnes (libellé + limite WIP souple)"));
    DATA.columns.forEach(function (col) {
      var row = el("div", "tax-row");
      var tagId = el("span", "badge", col.id);
      var inpLabel = document.createElement("input");
      inpLabel.type = "text";
      inpLabel.value = col.label;
      inpLabel.setAttribute("data-col-id", col.id);
      inpLabel.className = "s-col-label";
      var inpWip = document.createElement("input");
      inpWip.type = "number";
      inpWip.min = "1";
      inpWip.placeholder = "WIP";
      inpWip.style.maxWidth = "90px";
      inpWip.value = col.wip_limit !== null && col.wip_limit !== undefined ? String(col.wip_limit) : "";
      inpWip.setAttribute("data-col-id", col.id);
      inpWip.className = "s-col-wip";
      row.appendChild(tagId);
      row.appendChild(inpLabel);
      row.appendChild(inpWip);
      colBox.appendChild(row);
    });

    var hBox = document.getElementById("s-horizons-list");
    hBox.replaceChildren();
    hBox.appendChild(el("div", "empty-msg", "Horizons temporels"));
    DATA.horizons.forEach(function (h) {
      var row = el("div", "tax-row");
      row.appendChild(el("span", "badge", h.id));
      var inpH = document.createElement("input");
      inpH.type = "text";
      inpH.value = h.label;
      inpH.setAttribute("data-horizon-id", h.id);
      inpH.className = "s-horizon-label";
      row.appendChild(inpH);
      hBox.appendChild(row);
    });

    renderTaxonomyEditor("s-cats-list", DATA.categories || [], "category");
    renderTaxonomyEditor("s-tags-list", DATA.tags || [], "tag");
    document.getElementById("s-new-cat").value = "";
    document.getElementById("s-new-tag").value = "";
    document.getElementById("settings-modal-backdrop").classList.remove("hidden");
  }

  function closeSettingsModal() {
    document.getElementById("settings-modal-backdrop").classList.add("hidden");
  }

  document.getElementById("board-title").textContent = DATA.title;
  initTheme();
  loadFilters();
  syncFilterControlsFromState();
  populateSelects();

  document.getElementById("btn-new-card").addEventListener("click", openCreateModal);
  document.getElementById("btn-settings").addEventListener("click", openSettingsModal);
  document.getElementById("btn-refresh").addEventListener("click", refreshFromServer);

  document.querySelectorAll("#e-tabs .modal-tab").forEach(function (btn) {
    btn.addEventListener("click", function () { switchFormTabs("e-tabs", btn.getAttribute("data-tab")); });
  });
  document.querySelectorAll("#c-tabs .modal-tab").forEach(function (btn) {
    btn.addEventListener("click", function () { switchFormTabs("c-tabs", btn.getAttribute("data-tab")); });
  });

  ["e-horizon", "e-start", "e-due", "e-defer"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", function () {
      var t = activeModalId ? findTaskById(activeModalId) : null;
      updateEditTabBadges(t);
    });
  });
  ["c-horizon", "c-start", "c-due", "c-defer"].forEach(function (id) {
    document.getElementById(id).addEventListener("change", updateCreateTabBadges);
  });

  document.getElementById("e-dep-search").addEventListener("input", function () {
    var t = activeModalId ? findTaskById(activeModalId) : null;
    renderDepPicker("e-dep-picker", "e-dep-search", activeModalId || "", editDepsSelected, function () { updateEditTabBadges(t); });
  });
  document.getElementById("c-dep-search").addEventListener("input", function () {
    renderDepPicker("c-dep-picker", "c-dep-search", "", createDepsSelected, updateCreateTabBadges);
  });

  document.getElementById("e-preview-toggle").addEventListener("click", function () {
    var ta = document.getElementById("e-body");
    var pv = document.getElementById("e-body-preview");
    var showPv = pv.classList.contains("hidden");
    if (showPv) renderMarkdownSafe(pv, ta.value);
    pv.classList.toggle("hidden", !showPv);
    ta.classList.toggle("hidden", showPv);
    this.textContent = showPv ? "Éditer" : "Aperçu Markdown";
  });
  document.getElementById("c-preview-toggle").addEventListener("click", function () {
    var ta = document.getElementById("c-body");
    var pv = document.getElementById("c-body-preview");
    var showPv = pv.classList.contains("hidden");
    if (showPv) renderMarkdownSafe(pv, ta.value);
    pv.classList.toggle("hidden", !showPv);
    ta.classList.toggle("hidden", showPv);
    this.textContent = showPv ? "Éditer" : "Aperçu Markdown";
  });

  document.getElementById("btn-edit-card").addEventListener("click", function () {
    var t = activeModalId ? findTaskById(activeModalId) : null;
    if (!t) return;
    populateFormSelects(
      document.getElementById("e-status"),
      document.getElementById("e-priority"),
      document.getElementById("e-horizon"),
      t.status,
      t.priority,
      t.horizon
    );
    document.getElementById("e-title").value = t.title || "";
    document.getElementById("e-category").value = t.category || "";
    document.getElementById("e-assignee").value = t.assignee || "";
    document.getElementById("e-tags").value = (t.tags || []).join(", ");
    document.getElementById("e-start").value = t.start_date || "";
    document.getElementById("e-due").value = t.due_date || "";
    document.getElementById("e-defer").value = t.defer_until || "";
    document.getElementById("e-blocked").checked = Boolean(t.blocked);
    document.getElementById("e-blocked-reason").value = t.blocked_reason || "";
    document.getElementById("e-body").value = t.body || "";
    editDepsSelected = (t.depends_on || []).slice();
    document.getElementById("e-dep-search").value = "";
    renderDepPicker("e-dep-picker", "e-dep-search", t.id, editDepsSelected, function () { updateEditTabBadges(t); });
    renderAttachmentsList("e-atts-list", t, true);
    document.getElementById("e-upload-file").value = "";
    document.getElementById("e-attach-ref-input").value = "";
    updateEditTabBadges(t);
    switchFormTabs("e-tabs", "e-tab-general");
    setModalEditMode(true);
  });

  document.getElementById("e-upload-btn").addEventListener("click", function () {
    if (!activeModalId) return;
    var tid = activeModalId;
    var inp = document.getElementById("e-upload-file");
    if (!inp.files || !inp.files[0]) {
      showToast("Choisissez d'abord un fichier.");
      return;
    }
    var file = inp.files[0];
    if (file.size > 5 * 1024 * 1024) {
      showToast("Fichier trop volumineux (max 5 Mo).");
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      var resStr = String(reader.result || "");
      var idx = resStr.indexOf("base64,");
      var b64 = idx !== -1 ? resStr.slice(idx + 7) : resStr;
      fetch("/api/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Kanban-Token": DATA.csrf_token || ""
        },
        body: JSON.stringify({ id: tid, filename: file.name, content_base64: b64 })
      }).then(function (r) {
        return r.json().then(function (b) { return { ok: r.ok, body: b }; });
      }).then(function (resp) {
        if (!resp.ok || !resp.body.ok) {
          showToast("Erreur : " + (resp.body.error || "upload refusé"));
          return;
        }
        DATA = resp.body.state;
        populateSelects();
        renderMain();
        inp.value = "";
        var updated = findTaskById(tid);
        if (updated) {
          renderAttachmentsList("e-atts-list", updated, true);
          renderAttachmentsList("m-atts-list", updated, true);
          updateEditTabBadges(updated);
        }
        showToast("Fichier joint");
      }).catch(function () {
        showToast("Échec de l'envoi du fichier.");
      });
    };
    reader.readAsDataURL(file);
  });

  document.getElementById("e-attach-ref-btn").addEventListener("click", function () {
    if (!activeModalId) return;
    var tid = activeModalId;
    var inp = document.getElementById("e-attach-ref-input");
    var refVal = inp.value.trim();
    if (!refVal) return;
    runApiAction({ op: "attach_ref", id: tid, ref: refVal }, function () {
      inp.value = "";
      var updated = findTaskById(tid);
      if (updated) {
        renderAttachmentsList("e-atts-list", updated, true);
        renderAttachmentsList("m-atts-list", updated, true);
        updateEditTabBadges(updated);
      }
      showToast("Référence ajoutée");
    });
  });

  document.getElementById("btn-archive-card").addEventListener("click", function () {
    if (!activeModalId) return;
    var tid = activeModalId;
    runApiAction({ op: "archive", id: tid }, function () {
      closeModal();
      showToast(tid + " archivée");
    });
  });

  document.getElementById("btn-delete-card").addEventListener("click", function () {
    document.getElementById("m-delete-msg").textContent = "Confirmer la suppression définitive de cette carte ?";
    document.getElementById("btn-delete-force").classList.add("hidden");
    document.getElementById("m-delete-bar").classList.remove("hidden");
  });
  document.getElementById("btn-delete-cancel").addEventListener("click", function () {
    document.getElementById("m-delete-bar").classList.add("hidden");
  });
  document.getElementById("btn-delete-confirm").addEventListener("click", function () {
    if (!activeModalId) return;
    var tid = activeModalId;
    runApiAction({ op: "delete", id: tid, force: false }, function () {
      closeModal();
      showToast(tid + " supprimée");
    }, function (errMsg) {
      document.getElementById("m-delete-msg").textContent = errMsg;
      document.getElementById("btn-delete-force").classList.remove("hidden");
    });
  });
  document.getElementById("btn-delete-force").addEventListener("click", function () {
    if (!activeModalId) return;
    var tid = activeModalId;
    runApiAction({ op: "delete", id: tid, force: true }, function () {
      closeModal();
      showToast(tid + " supprimée (dépendances nettoyées)");
    });
  });

  document.getElementById("e-cancel").addEventListener("click", function () { setModalEditMode(false); });
  document.getElementById("m-edit-section").addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (!activeModalId) return;
    var tid = activeModalId;
    var payload = {
      op: "save_card",
      id: tid,
      actor: "humain",
      title: document.getElementById("e-title").value,
      status: document.getElementById("e-status").value,
      priority: document.getElementById("e-priority").value,
      horizon: document.getElementById("e-horizon").value,
      category: document.getElementById("e-category").value,
      assignee: document.getElementById("e-assignee").value,
      tags: document.getElementById("e-tags").value,
      depends_on: editDepsSelected.slice(),
      start_date: document.getElementById("e-start").value,
      due_date: document.getElementById("e-due").value,
      defer_until: document.getElementById("e-defer").value,
      blocked: document.getElementById("e-blocked").checked,
      blocked_reason: document.getElementById("e-blocked-reason").value,
      body: document.getElementById("e-body").value
    };
    runApiAction(payload, function (res) {
      var updated = findTaskById(tid);
      if (updated) openModal(updated);
      else closeModal();
      if (!res || !res.wip_warning) showToast(tid + " enregistrée");
    });
  });

  document.getElementById("c-close").addEventListener("click", closeCreateModal);
  document.getElementById("c-cancel").addEventListener("click", closeCreateModal);
  document.getElementById("create-modal-backdrop").addEventListener("click", function (ev) {
    if (ev.target === this) closeCreateModal();
  });
  document.getElementById("c-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var subsRaw = document.getElementById("c-subtasks").value.split(/\r?\n/).map(function (s) { return s.trim(); }).filter(Boolean);
    var payload = {
      op: "add",
      actor: "humain",
      title: document.getElementById("c-title").value,
      status: document.getElementById("c-status").value,
      priority: document.getElementById("c-priority").value,
      horizon: document.getElementById("c-horizon").value,
      category: document.getElementById("c-category").value,
      assignee: document.getElementById("c-assignee").value,
      tags: document.getElementById("c-tags").value,
      depends_on: createDepsSelected.slice(),
      start_date: document.getElementById("c-start").value,
      due_date: document.getElementById("c-due").value,
      defer_until: document.getElementById("c-defer").value,
      subtasks: subsRaw,
      body: document.getElementById("c-body").value
    };
    runApiAction(payload, function (res) {
      closeCreateModal();
      showToast("Carte " + (res && res.id ? res.id : "") + " créée");
    });
  });

  document.querySelectorAll(".view-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".view-tab").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      state.view = btn.getAttribute("data-view");
      saveFilters();
      renderMain();
    });
  });

  document.getElementById("toggle-swimlanes").addEventListener("click", function () {
    state.swimlanes = !state.swimlanes;
    this.textContent = "Swimlanes : " + (state.swimlanes ? "Oui" : "Non");
    this.classList.toggle("active", state.swimlanes);
    saveFilters();
    renderMain();
  });

  document.getElementById("toggle-stats").addEventListener("click", function () {
    state.showStats = !state.showStats;
    this.classList.toggle("active", state.showStats);
    renderStats();
  });

  document.getElementById("toggle-theme").addEventListener("click", toggleTheme);
  document.getElementById("f-search").addEventListener("input", function () { state.search = this.value; saveFilters(); renderMain(); });
  document.getElementById("f-priority").addEventListener("change", function () { state.priority = this.value; saveFilters(); renderMain(); });
  document.getElementById("f-assignee").addEventListener("change", function () { state.assignee = this.value; saveFilters(); renderMain(); });
  document.getElementById("f-category").addEventListener("change", function () { state.category = this.value; saveFilters(); renderMain(); });
  document.getElementById("f-tag").addEventListener("change", function () { state.tag = this.value; saveFilters(); renderMain(); });

  document.querySelectorAll(".chip-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var flag = btn.getAttribute("data-flag");
      state.flags[flag] = !state.flags[flag];
      btn.classList.toggle("active", state.flags[flag]);
      saveFilters();
      renderMain();
    });
  });

  document.getElementById("f-reset").addEventListener("click", function () {
    state.search = ""; state.priority = ""; state.assignee = ""; state.category = ""; state.tag = "";
    Object.keys(state.flags).forEach(function (k) { state.flags[k] = false; });
    document.getElementById("f-search").value = "";
    document.getElementById("f-priority").value = "";
    document.getElementById("f-assignee").value = "";
    document.getElementById("f-category").value = "";
    document.getElementById("f-tag").value = "";
    document.querySelectorAll(".chip-btn").forEach(function (b) { b.classList.remove("active"); });
    saveFilters();
    populateSelects();
    renderMain();
  });

  document.getElementById("m-close").addEventListener("click", closeModal);
  document.getElementById("modal-backdrop").addEventListener("click", function (ev) {
    if (ev.target === this) closeModal();
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    if (!document.getElementById("settings-modal-backdrop").classList.contains("hidden")) {
      closeSettingsModal();
    } else if (!document.getElementById("create-modal-backdrop").classList.contains("hidden")) {
      closeCreateModal();
    } else if (!document.getElementById("modal-backdrop").classList.contains("hidden")) {
      closeModal();
    }
  });

  document.getElementById("s-close").addEventListener("click", closeSettingsModal);
  document.getElementById("settings-modal-backdrop").addEventListener("click", function (ev) {
    if (ev.target === this) closeSettingsModal();
  });
  document.getElementById("s-labels-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var columns = [];
    document.querySelectorAll(".s-col-label").forEach(function (inp) {
      var cid = inp.getAttribute("data-col-id");
      var wipInp = document.querySelector('.s-col-wip[data-col-id="' + cid + '"]');
      var wipRaw = wipInp ? wipInp.value.trim() : "";
      columns.push({ id: cid, label: inp.value.trim(), wip_limit: wipRaw ? parseInt(wipRaw, 10) : null });
    });
    var horizons = [];
    document.querySelectorAll(".s-horizon-label").forEach(function (inp) {
      horizons.push({ id: inp.getAttribute("data-horizon-id"), label: inp.value.trim() });
    });
    runApiAction({
      op: "save_settings",
      title: document.getElementById("s-title").value,
      category_label: document.getElementById("s-cat-label").value,
      tag_label: document.getElementById("s-tag-label").value,
      columns: columns,
      horizons: horizons
    }, function () {
      openSettingsModal();
      showToast("Libellés enregistrés");
    });
  });
  document.getElementById("s-add-cat").addEventListener("click", function () {
    var val = document.getElementById("s-new-cat").value.trim();
    if (!val) return;
    runApiAction({ op: "add_taxonomy", kind: "category", value: val }, function () {
      openSettingsModal();
      showToast("Catégorie ajoutée : " + val);
    });
  });
  document.getElementById("s-add-tag").addEventListener("click", function () {
    var val = document.getElementById("s-new-tag").value.trim().replace(/^#+/, "");
    if (!val) return;
    runApiAction({ op: "add_taxonomy", kind: "tag", value: val }, function () {
      openSettingsModal();
      showToast("Tag ajouté : #" + val);
    });
  });

  renderMain();
})();
</script>
</body>
</html>
"""


def build_html_payload(
    cfg: dict,
    enriched: list[dict],
    today_str: str | None = None,
    interactive: bool = False,
    csrf_token: str = "",
) -> dict:
    ref_date, _ = resolve_ref_datetime(today_str)
    horizons = cfg.get("horizons", DEFAULT_HORIZONS)
    stats = compute_board_stats(cfg, enriched)
    tasks_payload = []
    for t in enriched:
        tasks_payload.append({
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "status_label": t["status_label"],
            "role": t["role"],
            "priority": t["priority"],
            "tags": t["tags"],
            "assignee": t["assignee"],
            "category": t["category"],
            "horizon": t["horizon"],
            "start_date": t["start_date"],
            "due_date": t["due_date"],
            "defer_until": t["defer_until"],
            "depends_on": t["depends_on"],
            "unmet_dependencies": t["unmet_dependencies"],
            "unblocks": t["unblocks"],
            "attachments": t["attachments"],
            "implicitly_blocked": t["implicitly_blocked"],
            "blocked": t["blocked"],
            "blocked_reason": t["blocked_reason"],
            "is_deferred": t["is_deferred"],
            "is_overdue": t["is_overdue"],
            "is_stale": t["is_stale"],
            "entered_status_at": t["entered_status_at"],
            "age_in_status_days": t["age_in_status_days"],
            "age_in_status_hours": t["age_in_status_hours"],
            "subtasks_done": t["subtasks_done"],
            "subtasks_total": t["subtasks_total"],
            "subtasks": t["subtasks"],
            "archived": t["archived"],
            "body": t["body"],
            "history": t["meta"].get("history", []),
        })
    all_cats = set(str(c).strip() for c in (cfg.get("categories") or []) if str(c).strip())
    all_tags = set(str(tg).strip() for tg in (cfg.get("tags") or []) if str(tg).strip())
    for t in enriched:
        if t["category"]:
            all_cats.add(t["category"])
        for tg in t["tags"]:
            all_tags.add(tg)

    return {
        "title": cfg.get("title", "Tableau Kanban"),
        "today": ref_date.isoformat(),
        "generated_at": format_utc(utc_now()),
        "interactive": bool(interactive),
        "csrf_token": csrf_token if interactive else "",
        "labels": get_labels(cfg),
        "categories": sorted(all_cats),
        "tags": sorted(all_tags),
        "columns": cfg["columns"],
        "horizons": horizons,
        "horizon_labels": {h["id"]: h["label"] for h in horizons},
        "stats": stats,
        "tasks": tasks_payload,
    }


def render_kanban_html(
    cfg: dict,
    enriched: list[dict],
    today_str: str | None = None,
    interactive: bool = False,
    csrf_token: str = "",
) -> str:
    payload = build_html_payload(
        cfg,
        enriched,
        today_str=today_str,
        interactive=interactive,
        csrf_token=csrf_token,
    )
    page_title = html.escape(str(cfg.get("title", "Tableau Kanban")), quote=True)
    return (
        HTML_TEMPLATE
        .replace("__PAGE_TITLE__", page_title)
        .replace("__KANBAN_DATA__", safe_json_for_html(payload))
    )


def _render_board_unlocked(
    board_dir: Path,
    cfg: dict,
    write_html: bool = True,
    force_md: bool = False,
    today_str: str | None = None,
) -> dict:
    drift, current_sha = check_kanban_md_drift(board_dir, cfg)
    if drift and not force_md:
        raise KanbanError(
            "KANBAN.md a été modifié manuellement depuis le dernier rendu "
            "(ex. dans Obsidian). Exécutez 'sync-md' pour rapatrier vos modifications, "
            "ou utilisez '--force-md' pour écraser KANBAN.md."
        )
    ref_date, _ = resolve_ref_datetime(today_str)
    active, archived = load_all_records(board_dir)
    enriched = enrich_records(active, archived, cfg, today_str=today_str)
    md_text = render_kanban_markdown(board_dir, cfg, enriched, ref_date)
    atomic_write_text(board_dir / "KANBAN.md", md_text)
    cfg["last_rendered_md_sha256"] = sha256_text(md_text)
    save_config(board_dir, cfg)

    html_written = False
    if write_html:
        html_text = render_kanban_html(cfg, enriched, today_str=today_str)
        atomic_write_text(board_dir / "kanban.html", html_text)
        html_written = True

    return {
        "md_path": str(board_dir / "KANBAN.md"),
        "html_path": str(board_dir / "kanban.html") if html_written else None,
        "md_sha256": cfg["last_rendered_md_sha256"],
        "previous_sha256": current_sha,
    }


def _validate_task_fields(
    cfg: dict,
    status: str,
    priority: str,
    horizon: str,
    start_date: str,
    due_date: str,
    defer_until: str,
) -> None:
    col_map = get_column_map(cfg)
    if status not in col_map:
        raise KanbanError(f"Colonne inconnue : {status!r}. Disponibles : {', '.join(col_map)}")
    if priority not in PRIORITIES:
        raise KanbanError(f"Priorité inconnue : {priority!r}. Disponibles : {', '.join(PRIORITIES)}")
    if horizon:
        h_map = get_horizon_map(cfg)
        if horizon not in h_map:
            raise KanbanError(f"Horizon inconnu : {horizon!r}. Disponibles : {', '.join(h_map)}")
    s_dt = parse_date(start_date, "start_date") if start_date else None
    d_dt = parse_date(due_date, "due_date") if due_date else None
    if defer_until:
        parse_date(defer_until, "defer_until")
    if s_dt and d_dt and s_dt > d_dt:
        raise KanbanError(f"Plage invalide : start_date ({start_date}) > due_date ({due_date})")


def _assert_no_dependency_cycles(
    board_dir: Path,
    candidate_id: str,
    candidate_deps: list[str],
    all_ids: set[str],
) -> None:
    for dep in candidate_deps:
        if dep == candidate_id:
            raise KanbanError(f"Dépendance circulaire : {candidate_id} ne peut pas dépendre d'elle-même")
        if dep not in all_ids:
            raise KanbanError(f"Dépendance introuvable : {dep}")
    active, archived = load_all_records(board_dir)
    by_id = {str(r["meta"].get("id", "")): {"meta": dict(r["meta"])} for r in (active + archived)}
    by_id[candidate_id] = {"meta": {"id": candidate_id, "depends_on": list(candidate_deps)}}
    cycles = detect_dependency_cycles(by_id)
    if cycles:
        raise KanbanError("Cycle de dépendances détecté : " + " -> ".join(cycles[0]))


def _split_csv(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def sanitize_attachment_name(name: str) -> str:
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    if not base:
        raise KanbanError("Nom de pièce jointe vide")
    ext = Path(base).suffix.lower()
    if ext not in SAFE_ATTACHMENT_EXTS:
        allowed = ", ".join(sorted(SAFE_ATTACHMENT_EXTS))
        raise KanbanError(f"Extension non autorisée '{ext or '(aucune)'}'. Autorisées : {allowed}")
    stem = slugify(Path(base).stem, max_len=60)
    return f"{stem}{ext}"


def _validate_attachment_refs(refs: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in refs:
        val = str(raw or "").strip().replace("\\", "/")
        if not val:
            continue
        if ".." in Path(val).parts or val.startswith("/") or re.match(r"^[A-Za-z]:", val):
            raise KanbanError(f"Chemin de pièce jointe non autorisé : {raw!r}")
        if val not in seen:
            seen.add(val)
            cleaned.append(val)
    return cleaned


def _op_init(board_dir: Path, title: str, force: bool) -> dict:
    cfg_path = board_dir / "kanban.json"
    if cfg_path.exists() and not force:
        raise KanbanError(f"Un tableau existe déjà dans {board_dir}. Utilisez --force pour réinitialiser la config.")
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "tasks").mkdir(exist_ok=True)
    (board_dir / "archive").mkdir(exist_ok=True)

    gitignore = board_dir / ".gitignore"
    if not gitignore.exists():
        atomic_write_text(gitignore, ".kanban.lock\n# Décommenter si vous ne souhaitez pas versionner le dashboard HTML :\n# kanban.html\n")

    cfg = {
        "version": 1,
        "title": title or "Tableau Kanban",
        "id_prefix": "T",
        "next_id": 1,
        "stale_days": 7,
        "obsidian_compat": True,
        "last_rendered_md_sha256": "",
        "labels": dict(DEFAULT_LABELS),
        "categories": [],
        "tags": [],
        "columns": [dict(c) for c in DEFAULT_COLUMNS],
        "horizons": [dict(h) for h in DEFAULT_HORIZONS],
    }
    if force:
        cfg["next_id"] = compute_next_id_number(board_dir, cfg)
    save_config(board_dir, cfg)
    render_info = _render_board_unlocked(board_dir, cfg, write_html=True, force_md=force)
    return {"action": "init", "board_dir": str(board_dir), "title": cfg["title"], **render_info}


def _op_add(board_dir: Path, cfg: dict, params: dict) -> dict:
    title = str(params.get("title", "")).strip()
    if not title:
        raise KanbanError("Le titre de la tâche (--title) est obligatoire")
    status = str(params.get("status", "idees")).strip()
    priority = str(params.get("priority", "normale")).strip()
    horizon = str(params.get("horizon", "") or "").strip()
    start_date = str(params.get("start_date", "") or "").strip()
    due_date = str(params.get("due_date", "") or "").strip()
    defer_until = str(params.get("defer_until", "") or "").strip()
    _validate_task_fields(cfg, status, priority, horizon, start_date, due_date, defer_until)

    active, archived = load_all_records(board_dir)
    all_ids = {str(r["meta"].get("id", "")) for r in (active + archived)}
    prefix = cfg.get("id_prefix", "T")
    num = compute_next_id_number(board_dir, cfg)
    task_id = format_task_id(num, prefix)
    depends_on = _split_csv(params.get("depends_on"))
    _assert_no_dependency_cycles(board_dir, task_id, depends_on, all_ids)

    now_iso = format_utc(utc_now())
    actor = str(params.get("actor", "humain") or "humain").strip()
    assignee = str(params.get("assignee", "") or "").strip()
    meta: dict = {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": priority,
        "tags": _split_csv(params.get("tags")),
        "assignee": assignee,
        "claimed_at": now_iso if assignee else "",
        "category": str(params.get("category", "") or "").strip(),
        "horizon": horizon,
        "start_date": start_date,
        "due_date": due_date,
        "defer_until": defer_until,
        "depends_on": depends_on,
        "attachments": _validate_attachment_refs(_split_csv(params.get("attachments"))),
        "created_at": now_iso,
        "updated_at": now_iso,
        "history": [f"{now_iso} | creation -> {status} | {actor}"],
    }

    body_parts: list[str] = []
    raw_body = str(params.get("body", "") or "").strip()
    if raw_body:
        body_parts.append(raw_body)
    subtasks = [str(s).strip() for s in (params.get("subtasks") or []) if str(s).strip()]
    if subtasks:
        lines = ["## Sous-tâches"] + [f"- [ ] {s}" for s in subtasks]
        body_parts.append("\n".join(lines))
    body = "\n\n".join(body_parts)

    slug = slugify(title)
    filename = f"{task_id}-{slug}.md"
    target_path = safe_task_path(board_dir / "tasks", filename)
    fd = os.open(str(target_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    os.close(fd)
    write_task_record(target_path, meta, body)

    cfg["next_id"] = num + 1
    save_config(board_dir, cfg)
    return {"action": "add", "id": task_id, "file": filename, "status": status, "title": title}


def _op_edit(board_dir: Path, cfg: dict, params: dict) -> dict:
    active, archived = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    meta = dict(rec["meta"])
    body = rec["body"]

    for key in ("title", "priority", "assignee", "category", "horizon", "start_date", "due_date", "defer_until"):
        if key in params and params[key] is not None:
            meta[key] = str(params[key]).strip()
    if "tags" in params and params["tags"] is not None:
        meta["tags"] = _split_csv(params["tags"])
    if "add_tags" in params and params["add_tags"]:
        existing = list(meta.get("tags") or [])
        for tg in _split_csv(params["add_tags"]):
            if tg not in existing:
                existing.append(tg)
        meta["tags"] = existing
    if "depends_on" in params and params["depends_on"] is not None:
        meta["depends_on"] = _split_csv(params["depends_on"])
    if "attachments" in params and params["attachments"] is not None:
        meta["attachments"] = _validate_attachment_refs(_split_csv(params["attachments"]))

    _validate_task_fields(
        cfg,
        str(meta.get("status", "")),
        str(meta.get("priority", "normale")),
        str(meta.get("horizon", "") or ""),
        str(meta.get("start_date", "") or ""),
        str(meta.get("due_date", "") or ""),
        str(meta.get("defer_until", "") or ""),
    )
    all_ids = {str(r["meta"].get("id", "")) for r in (active + archived)}
    _assert_no_dependency_cycles(board_dir, str(meta["id"]), [str(d) for d in (meta.get("depends_on") or [])], all_ids)

    if "body" in params and params["body"] is not None:
        body = str(params["body"]).replace("\r\n", "\n").replace("\r", "\n").strip()
    if params.get("append_note"):
        note = str(params["append_note"]).strip()
        body = (body.rstrip("\n") + "\n\n" + note).lstrip("\n") if body.strip() else note

    meta["updated_at"] = format_utc(utc_now())
    write_task_record(rec["path"], meta, body)
    return {"action": "edit", "id": meta["id"], "file": rec["filename"]}


def _op_save_card(board_dir: Path, cfg: dict, params: dict) -> dict:
    active, archived = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    meta = dict(rec["meta"])
    body = rec["body"]

    if "title" in params and params["title"] is not None:
        title = str(params["title"]).strip()
        if not title:
            raise KanbanError("Le titre de la carte ne peut pas être vide")
        meta["title"] = title
    for key in ("priority", "assignee", "category", "horizon", "start_date", "due_date", "defer_until"):
        if key in params and params[key] is not None:
            meta[key] = str(params[key]).strip()
    if "tags" in params and params["tags"] is not None:
        meta["tags"] = _split_csv(params["tags"])
    if "depends_on" in params and params["depends_on"] is not None:
        meta["depends_on"] = _split_csv(params["depends_on"])
    if "attachments" in params and params["attachments"] is not None:
        meta["attachments"] = _validate_attachment_refs(_split_csv(params["attachments"]))

    new_status = str(params.get("status") or meta.get("status", "")).strip()
    _validate_task_fields(
        cfg,
        new_status,
        str(meta.get("priority", "normale")),
        str(meta.get("horizon", "") or ""),
        str(meta.get("start_date", "") or ""),
        str(meta.get("due_date", "") or ""),
        str(meta.get("defer_until", "") or ""),
    )
    all_ids = {str(r["meta"].get("id", "")) for r in (active + archived)}
    _assert_no_dependency_cycles(
        board_dir,
        str(meta["id"]),
        [str(d) for d in (meta.get("depends_on") or [])],
        all_ids,
    )

    if "blocked" in params and params["blocked"] is not None:
        is_blocked = bool(params["blocked"])
        meta["blocked"] = is_blocked
        meta["blocked_reason"] = str(params.get("blocked_reason", "") or "").strip() if is_blocked else ""
    if "body" in params and params["body"] is not None:
        body = str(params["body"]).replace("\r\n", "\n").replace("\r", "\n").strip()

    now_iso = format_utc(utc_now())
    actor = str(params.get("actor", "humain") or "humain").strip()
    old_status = str(meta.get("status", ""))
    if old_status != new_status:
        history = list(meta.get("history") or [])
        history.append(f"{now_iso} | {old_status} -> {new_status} | {actor}")
        meta["history"] = history
        meta["status"] = new_status

    meta["updated_at"] = now_iso
    write_task_record(rec["path"], meta, body)

    wip_warning = None
    col = get_column_map(cfg)[new_status]
    if col.get("wip_limit") is not None:
        active_after, _ = load_all_records(board_dir)
        cnt = sum(1 for r in active_after if r["meta"].get("status") == new_status)
        if cnt > int(col["wip_limit"]):
            wip_warning = f"Alerte WIP souple : '{col['label']}' contient {cnt}/{col['wip_limit']} cartes."

    return {
        "action": "save_card",
        "id": meta["id"],
        "status": new_status,
        "wip_warning": wip_warning,
    }


def _op_delete(board_dir: Path, cfg: dict, task_id: str, force: bool = False) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, task_id)
    tid = str(rec["meta"]["id"])

    dependents = [
        r for r in active
        if str(r["meta"].get("id", "")) != tid and tid in [str(d) for d in (r["meta"].get("depends_on") or [])]
    ]
    if dependents and not force:
        details = ", ".join(f"{r['meta']['id']} ({r['meta'].get('title', '')})" for r in dependents)
        raise KanbanError(
            f"Impossible de supprimer {tid} : {len(dependents)} carte(s) en dépend(ent) : {details}. "
            "Retirez la dépendance ou confirmez avec --force."
        )

    removed_from: list[str] = []
    if dependents and force:
        now_iso = format_utc(utc_now())
        for dep_rec in dependents:
            dep_meta = dict(dep_rec["meta"])
            dep_meta["depends_on"] = [d for d in (dep_meta.get("depends_on") or []) if str(d) != tid]
            dep_meta["updated_at"] = now_iso
            write_task_record(dep_rec["path"], dep_meta, dep_rec["body"])
            removed_from.append(str(dep_meta["id"]))

    target_path = safe_task_path(board_dir / "tasks", rec["filename"])
    target_path.unlink()

    att_root = (board_dir / "attachments").resolve()
    card_att_dir = (att_root / tid).resolve()
    if card_att_dir.parent == att_root and card_att_dir.is_dir():
        shutil.rmtree(card_att_dir, ignore_errors=True)

    return {"action": "delete", "id": tid, "removed_from_dependents": removed_from}


def _op_attach(
    board_dir: Path,
    cfg: dict,
    task_id: str,
    source_path: str | None = None,
    filename: str | None = None,
    raw_bytes: bytes | None = None,
    ref_only: str | None = None,
) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, task_id)
    meta = dict(rec["meta"])
    tid = str(meta["id"])
    attachments = list(meta.get("attachments") or [])

    if ref_only is not None:
        cleaned = _validate_attachment_refs([ref_only])
        if not cleaned:
            raise KanbanError("Référence de pièce jointe vide")
        entry = cleaned[0]
    else:
        if source_path:
            src = Path(source_path)
            if not src.is_file():
                raise KanbanError(f"Fichier introuvable : {source_path}")
            if src.stat().st_size > MAX_ATTACHMENT_BYTES:
                raise KanbanError("Pièce jointe trop volumineuse (max 5 Mo)")
            data = src.read_bytes()
            orig_name = filename or src.name
        elif raw_bytes is not None:
            if len(raw_bytes) > MAX_ATTACHMENT_BYTES:
                raise KanbanError("Pièce jointe trop volumineuse (max 5 Mo)")
            data = raw_bytes
            orig_name = filename or ""
        else:
            raise KanbanError("Précisez un fichier (--file) ou une référence (--ref)")

        safe_name = sanitize_attachment_name(orig_name)
        att_root = (board_dir / "attachments").resolve()
        att_dir = (att_root / tid).resolve()
        if att_dir.parent != att_root:
            raise KanbanError("Dossier de pièce jointe invalide")
        att_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_task_path(att_dir, safe_name)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{safe_name}.", suffix=".tmp", dir=str(att_dir))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, dest)
        except Exception:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
        entry = f"attachments/{tid}/{safe_name}"

    if entry not in attachments:
        attachments.append(entry)
    meta["attachments"] = attachments
    meta["updated_at"] = format_utc(utc_now())
    write_task_record(rec["path"], meta, rec["body"])
    return {"action": "attach", "id": tid, "attachment": entry, "attachments": attachments}


def _op_detach(board_dir: Path, cfg: dict, task_id: str, target: str) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, task_id)
    meta = dict(rec["meta"])
    tid = str(meta["id"])
    needle = str(target or "").strip().replace("\\", "/")
    if not needle:
        raise KanbanError("Précisez la pièce jointe à retirer")

    attachments = list(meta.get("attachments") or [])
    kept: list[str] = []
    removed: list[str] = []
    for att in attachments:
        if att == needle or Path(att).name == needle:
            removed.append(att)
        else:
            kept.append(att)
    if not removed:
        raise KanbanError(f"Pièce jointe introuvable sur {tid} : {needle!r}")

    att_root = (board_dir / "attachments").resolve()
    card_att_dir = (att_root / tid).resolve()
    for rem in removed:
        if rem.startswith(f"attachments/{tid}/") and card_att_dir.parent == att_root:
            with contextlib.suppress(Exception):
                candidate = safe_task_path(card_att_dir, Path(rem).name)
                if candidate.is_file():
                    candidate.unlink()

    meta["attachments"] = kept
    meta["updated_at"] = format_utc(utc_now())
    write_task_record(rec["path"], meta, rec["body"])
    return {"action": "detach", "id": tid, "removed": removed, "attachments": kept}


def _op_move(board_dir: Path, cfg: dict, params: dict) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    meta = dict(rec["meta"])
    target_status = str(params["status"]).strip()
    col_map = get_column_map(cfg)
    if target_status not in col_map:
        raise KanbanError(f"Colonne inconnue : {target_status!r}. Disponibles : {', '.join(col_map)}")

    actor = str(params.get("actor", "humain") or "humain").strip()
    as_agent = bool(params.get("as_agent", False)) or actor.lower().startswith("agent:")
    force_done = bool(params.get("force_done", False))
    target_col = col_map[target_status]
    if as_agent and target_col.get("role") == "done" and not force_done:
        review_cols = [c["id"] for c in cfg["columns"] if c.get("role") == "review"]
        hint = review_cols[0] if review_cols else "en_revue"
        raise KanbanError(
            f"Garde-fou Agent/Humain : un agent ne peut pas valider '{meta['id']}' directement en "
            f"'{target_status}'. Déplacez-la vers '{hint}' pour validation humaine."
        )

    old_status = str(meta.get("status", ""))
    now_iso = format_utc(utc_now())
    if old_status != target_status:
        history = list(meta.get("history") or [])
        history.append(f"{now_iso} | {old_status} -> {target_status} | {actor}")
        meta["history"] = history
        meta["status"] = target_status
    meta["updated_at"] = now_iso

    body = rec["body"]
    if params.get("note"):
        note = str(params["note"]).strip()
        body = (body.rstrip("\n") + f"\n\n- Note ({now_iso}, {actor}) : {note}").lstrip("\n")

    write_task_record(rec["path"], meta, body)

    wip_warning = None
    limit = target_col.get("wip_limit")
    if limit is not None:
        active_after, _ = load_all_records(board_dir)
        cnt = sum(1 for r in active_after if r["meta"].get("status") == target_status)
        if cnt > int(limit):
            wip_warning = f"Alerte WIP souple : '{target_col['label']}' contient {cnt}/{limit} cartes."

    return {
        "action": "move",
        "id": meta["id"],
        "from": old_status,
        "to": target_status,
        "wip_warning": wip_warning,
    }


def _op_claim(board_dir: Path, cfg: dict, params: dict) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    meta = dict(rec["meta"])
    by = str(params.get("by", "")).strip()
    if not by:
        raise KanbanError("Le paramètre --by <acteur> est obligatoire pour claim")

    current_assignee = str(meta.get("assignee", "") or "").strip()
    force = bool(params.get("force", False))
    if current_assignee and current_assignee != by and not force:
        raise KanbanError(
            f"La carte {meta['id']} est déjà prise par '{current_assignee}' "
            f"(depuis {meta.get('claimed_at') or 'date inconnue'}). Utilisez --force pour réassigner."
        )

    now_iso = format_utc(utc_now())
    meta["assignee"] = by
    meta["claimed_at"] = now_iso
    meta["updated_at"] = now_iso

    moved_to = None
    if params.get("start"):
        in_prog = [c["id"] for c in cfg["columns"] if c.get("role") == "in_progress"]
        if in_prog and meta.get("status") != in_prog[0]:
            old_status = str(meta.get("status", ""))
            history = list(meta.get("history") or [])
            history.append(f"{now_iso} | {old_status} -> {in_prog[0]} | {by}")
            meta["history"] = history
            meta["status"] = in_prog[0]
            moved_to = in_prog[0]

    write_task_record(rec["path"], meta, rec["body"])
    return {"action": "claim", "id": meta["id"], "assignee": by, "moved_to": moved_to}


def _op_release(board_dir: Path, params: dict) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    meta = dict(rec["meta"])
    previous = str(meta.get("assignee", "") or "")
    meta["assignee"] = ""
    meta["claimed_at"] = ""
    meta["updated_at"] = format_utc(utc_now())
    write_task_record(rec["path"], meta, rec["body"])
    return {"action": "release", "id": meta["id"], "previous_assignee": previous}


def _op_block(board_dir: Path, params: dict, blocked: bool) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    meta = dict(rec["meta"])
    if blocked:
        reason = str(params.get("reason", "") or "").strip()
        if not reason:
            raise KanbanError("Un motif (--reason) est obligatoire pour bloquer une carte")
        meta["blocked"] = True
        meta["blocked_reason"] = reason
    else:
        meta["blocked"] = False
        meta["blocked_reason"] = ""
    meta["updated_at"] = format_utc(utc_now())
    write_task_record(rec["path"], meta, rec["body"])
    return {
        "action": "block" if blocked else "unblock",
        "id": meta["id"],
        "blocked": blocked,
        "reason": meta.get("blocked_reason", ""),
    }


def _op_toggle_subtask(board_dir: Path, params: dict, checked: bool) -> dict:
    active, _ = load_all_records(board_dir)
    rec = find_task_record(active, str(params["id"]))
    target = str(params.get("target", "")).strip()
    if not target:
        raise KanbanError("Précisez l'index (1, 2, ...) ou un extrait de la sous-tâche")

    lines = rec["body"].replace("\r\n", "\n").split("\n")
    _, _, items = extract_subtasks(rec["body"])
    if not items:
        raise KanbanError(f"Aucune sous-tâche '- [ ]' trouvée dans {rec['meta']['id']}")

    matched: dict | None = None
    if target.isdigit():
        idx_num = int(target)
        for item in items:
            if item["index"] == idx_num:
                matched = item
                break
    else:
        needle = target.lower()
        hits = [item for item in items if needle in item["text"].lower()]
        if len(hits) > 1:
            raise KanbanError(f"Plusieurs sous-tâches correspondent à {target!r} ; utilisez l'index numérique")
        if hits:
            matched = hits[0]

    if not matched:
        raise KanbanError(f"Sous-tâche introuvable dans {rec['meta']['id']} : {target!r}")

    m = CHECKBOX_RE.match(lines[matched["line_idx"]])
    assert m is not None
    lines[matched["line_idx"]] = f"{m.group(1)}{'x' if checked else ' '}{m.group(3)}"
    meta = dict(rec["meta"])
    meta["updated_at"] = format_utc(utc_now())
    write_task_record(rec["path"], meta, "\n".join(lines))
    return {
        "action": "check" if checked else "uncheck",
        "id": meta["id"],
        "subtask": matched["text"],
        "done": checked,
    }


def _op_archive(board_dir: Path, cfg: dict, params: dict) -> dict:
    active, archived = load_all_records(board_dir)
    today_str = params.get("today")
    enriched = enrich_records(active, archived, cfg, today_str=today_str)
    active_enriched = [t for t in enriched if not t["archived"]]

    to_archive: list[dict] = []
    if params.get("id"):
        target = find_task_record(active_enriched, str(params["id"]))
        if target["role"] != "done" and not params.get("force"):
            raise KanbanError(
                f"{target['id']} n'est pas dans une colonne Terminé (statut actuel : {target['status']})."
            )
        to_archive.append(target)
    elif params.get("all_done"):
        to_archive = [t for t in active_enriched if t["role"] == "done"]
    elif params.get("done_older_than") is not None:
        min_days = int(params["done_older_than"])
        to_archive = [
            t for t in active_enriched
            if t["role"] == "done" and t["age_in_status_days"] >= min_days
        ]
    else:
        raise KanbanError("Précisez --id <ID>, --all-done ou --done-older-than <JOURS>")

    archive_dir = board_dir / "archive"
    archive_dir.mkdir(exist_ok=True)
    moved_ids: list[str] = []
    for item in to_archive:
        src = Path(item["path"])
        dst = safe_task_path(archive_dir, item["filename"])
        if dst.exists():
            raise KanbanError(f"Conflit d'archivage : {dst} existe déjà")
        os.replace(src, dst)
        moved_ids.append(item["id"])

    return {"action": "archive", "archived_ids": moved_ids, "count": len(moved_ids)}


def _op_config(board_dir: Path, cfg: dict, params: dict) -> dict:
    if params.get("title"):
        cfg["title"] = str(params["title"]).strip()
    if params.get("stale_days") is not None:
        days = int(params["stale_days"])
        if days < 1:
            raise KanbanError("stale_days doit être >= 1")
        cfg["stale_days"] = days
    if params.get("obsidian") is not None:
        cfg["obsidian_compat"] = str(params["obsidian"]).lower() in ("1", "true", "oui", "yes")
    labels = get_labels(cfg)
    if params.get("category_label"):
        labels["category"] = str(params["category_label"]).strip()
    if params.get("tag_label"):
        labels["tag"] = str(params["tag_label"]).strip()
    cfg["labels"] = labels

    if "categories" in params and params["categories"] is not None:
        cfg["categories"] = sorted(set(_split_csv(params["categories"])))
    if "tags" in params and params["tags"] is not None:
        cfg["tags"] = sorted(set(t.lstrip("#") for t in _split_csv(params["tags"]) if t.lstrip("#")))

    for spec in params.get("rename_column") or []:
        if "=" not in spec:
            raise KanbanError(f"Format attendu pour --rename-column : COL=Libellé ({spec!r})")
        col_id, new_label = spec.split("=", 1)
        col_id = col_id.strip()
        new_label = new_label.strip()
        if not new_label:
            raise KanbanError("Le nouveau libellé de colonne ne peut pas être vide")
        found = False
        for col in cfg["columns"]:
            if col["id"] == col_id:
                col["label"] = new_label
                found = True
        if not found:
            raise KanbanError(f"Colonne inconnue dans --rename-column : {col_id!r}")

    for spec in params.get("rename_horizon") or []:
        if "=" not in spec:
            raise KanbanError(f"Format attendu pour --rename-horizon : HORIZON=Libellé ({spec!r})")
        h_id, new_label = spec.split("=", 1)
        h_id = h_id.strip()
        new_label = new_label.strip()
        if not new_label:
            raise KanbanError("Le nouveau libellé d'horizon ne peut pas être vide")
        horizons = cfg.setdefault("horizons", [dict(h) for h in DEFAULT_HORIZONS])
        found = False
        for h in horizons:
            if h["id"] == h_id:
                h["label"] = new_label
                found = True
        if not found:
            raise KanbanError(f"Horizon inconnu dans --rename-horizon : {h_id!r}")

    for spec in params.get("set_wip") or []:
        if "=" not in spec:
            raise KanbanError(f"Format attendu pour --set-wip : COL=N ou COL=none ({spec!r})")
        col_id, raw_val = spec.split("=", 1)
        col_id = col_id.strip()
        raw_val = raw_val.strip().lower()
        found = False
        for col in cfg["columns"]:
            if col["id"] == col_id:
                found = True
                if raw_val in ("none", "null", "0", ""):
                    col["wip_limit"] = None
                else:
                    n = int(raw_val)
                    if n < 1:
                        raise KanbanError("La limite WIP doit être >= 1 ou 'none'")
                    col["wip_limit"] = n
        if not found:
            raise KanbanError(f"Colonne inconnue dans --set-wip : {col_id!r}")
    save_config(board_dir, cfg)
    return {
        "action": "config",
        "title": cfg["title"],
        "stale_days": cfg["stale_days"],
        "obsidian_compat": cfg["obsidian_compat"],
        "labels": cfg["labels"],
    }


def _op_save_settings(board_dir: Path, cfg: dict, params: dict) -> dict:
    title = str(params.get("title", "") or "").strip()
    if not title:
        raise KanbanError("Le titre du tableau ne peut pas être vide")
    cfg["title"] = title
    labels = get_labels(cfg)
    cat_lbl = str(params.get("category_label", "") or "").strip()
    tag_lbl = str(params.get("tag_label", "") or "").strip()
    if cat_lbl:
        labels["category"] = cat_lbl
    if tag_lbl:
        labels["tag"] = tag_lbl
    cfg["labels"] = labels

    if isinstance(params.get("columns"), list):
        by_id = {c["id"]: c for c in cfg["columns"]}
        for item in params["columns"]:
            cid = str(item.get("id", "")).strip()
            if cid not in by_id:
                raise KanbanError(f"Colonne inconnue : {cid!r}")
            lbl = str(item.get("label", "") or "").strip()
            if not lbl:
                raise KanbanError(f"Libellé vide pour la colonne {cid!r}")
            by_id[cid]["label"] = lbl
            wip = item.get("wip_limit")
            if wip is None or wip == "":
                by_id[cid]["wip_limit"] = None
            else:
                n = int(wip)
                if n < 1:
                    raise KanbanError("La limite WIP doit être >= 1")
                by_id[cid]["wip_limit"] = n

    if isinstance(params.get("horizons"), list):
        horizons = cfg.setdefault("horizons", [dict(h) for h in DEFAULT_HORIZONS])
        h_by_id = {h["id"]: h for h in horizons}
        for item in params["horizons"]:
            hid = str(item.get("id", "")).strip()
            if hid not in h_by_id:
                raise KanbanError(f"Horizon inconnu : {hid!r}")
            lbl = str(item.get("label", "") or "").strip()
            if not lbl:
                raise KanbanError(f"Libellé vide pour l'horizon {hid!r}")
            h_by_id[hid]["label"] = lbl

    save_config(board_dir, cfg)
    return {"action": "save_settings", "title": cfg["title"], "labels": cfg["labels"]}


def _op_rename_category(board_dir: Path, cfg: dict, old_name: str, new_name: str) -> dict:
    old_clean = str(old_name or "").strip()
    new_clean = str(new_name or "").strip()
    if not old_clean:
        raise KanbanError("L'ancien nom de catégorie est obligatoire")

    active, _ = load_all_records(board_dir)
    now_iso = format_utc(utc_now())
    updated = 0
    for rec in active:
        meta = dict(rec["meta"])
        if str(meta.get("category", "") or "").strip() == old_clean:
            meta["category"] = new_clean
            meta["updated_at"] = now_iso
            write_task_record(rec["path"], meta, rec["body"])
            updated += 1

    cats = [str(c).strip() for c in (cfg.get("categories") or []) if str(c).strip()]
    new_cats: list[str] = []
    seen: set[str] = set()
    replaced = False
    for c in cats:
        target = new_clean if c == old_clean else c
        if c == old_clean:
            replaced = True
        if target and target not in seen:
            seen.add(target)
            new_cats.append(target)
    if new_clean and not replaced and new_clean not in seen:
        new_cats.append(new_clean)
    cfg["categories"] = sorted(new_cats)
    save_config(board_dir, cfg)
    return {"action": "rename-category", "old": old_clean, "new": new_clean, "updated_cards": updated}


def _op_rename_tag(board_dir: Path, cfg: dict, old_tag: str, new_tag: str) -> dict:
    old_clean = str(old_tag or "").strip().lstrip("#")
    new_clean = str(new_tag or "").strip().lstrip("#")
    if not old_clean:
        raise KanbanError("L'ancien tag est obligatoire")

    active, _ = load_all_records(board_dir)
    now_iso = format_utc(utc_now())
    updated = 0
    for rec in active:
        meta = dict(rec["meta"])
        tags = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()]
        if old_clean in tags:
            deduped: list[str] = []
            seen: set[str] = set()
            for t in tags:
                mapped = new_clean if t == old_clean else t
                if mapped and mapped not in seen:
                    seen.add(mapped)
                    deduped.append(mapped)
            meta["tags"] = deduped
            meta["updated_at"] = now_iso
            write_task_record(rec["path"], meta, rec["body"])
            updated += 1

    cfg_tags = [str(t).strip() for t in (cfg.get("tags") or []) if str(t).strip()]
    new_cfg_tags: list[str] = []
    seen_cfg: set[str] = set()
    replaced = False
    for t in cfg_tags:
        mapped = new_clean if t == old_clean else t
        if t == old_clean:
            replaced = True
        if mapped and mapped not in seen_cfg:
            seen_cfg.add(mapped)
            new_cfg_tags.append(mapped)
    if new_clean and not replaced and new_clean not in seen_cfg:
        new_cfg_tags.append(new_clean)
    cfg["tags"] = sorted(new_cfg_tags)
    save_config(board_dir, cfg)
    return {"action": "rename-tag", "old": old_clean, "new": new_clean, "updated_cards": updated}


def _op_add_taxonomy(board_dir: Path, cfg: dict, kind: str, value: str) -> dict:
    kind_clean = str(kind or "").strip().lower()
    if kind_clean not in ("category", "tag"):
        raise KanbanError(f"Type inconnu : {kind!r} (attendu: category | tag)")
    val = str(value or "").strip()
    if kind_clean == "tag":
        val = val.lstrip("#").strip()
    if not val:
        raise KanbanError("La valeur à ajouter ne peut pas être vide")
    key = "categories" if kind_clean == "category" else "tags"
    items = sorted(set(str(x).strip() for x in (cfg.get(key) or []) if str(x).strip()) | {val})
    cfg[key] = items
    save_config(board_dir, cfg)
    return {"action": "add_taxonomy", "kind": kind_clean, "value": val, key: items}


def _op_sync_md(board_dir: Path, cfg: dict, actor: str, dry_run: bool) -> dict:
    md_path = board_dir / "KANBAN.md"
    if not md_path.exists():
        raise KanbanError("KANBAN.md introuvable")
    md_text = md_path.read_text(encoding="utf-8").replace("\r\n", "\n")

    col_lookup: dict[str, str] = {}
    for col in cfg["columns"]:
        col_lookup[col["label"].strip().lower()] = col["id"]
        col_lookup[col["id"].strip().lower()] = col["id"]

    prefix = re.escape(cfg.get("id_prefix", "T"))
    id_re = re.compile(rf"\b({prefix}-\d+)\b")
    active, _ = load_all_records(board_dir)
    active_by_id = {str(r["meta"].get("id", "")).upper(): r for r in active}

    current_col_id: str | None = None
    changes: list[dict] = []
    seen_ids: set[str] = set()
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            heading_clean = re.sub(r"\s*\(\d+(?:/\d+)?\)\s*$", "", heading).strip().lower()
            current_col_id = col_lookup.get(heading_clean)
            continue
        if current_col_id and re.match(r"^\s*[-*]\s+\[[ xX]\]\s+", line):
            m = id_re.search(line)
            if not m:
                continue
            tid = m.group(1).upper()
            if tid in seen_ids or tid not in active_by_id:
                continue
            seen_ids.add(tid)
            rec = active_by_id[tid]
            old_status = str(rec["meta"].get("status", ""))
            if old_status != current_col_id:
                changes.append({"id": rec["meta"]["id"], "from": old_status, "to": current_col_id})

    if not dry_run:
        now_iso = format_utc(utc_now())
        for ch in changes:
            rec = active_by_id[ch["id"].upper()]
            meta = dict(rec["meta"])
            history = list(meta.get("history") or [])
            history.append(f"{now_iso} | {ch['from']} -> {ch['to']} | {actor} (sync-md)")
            meta["status"] = ch["to"]
            meta["updated_at"] = now_iso
            meta["history"] = history
            write_task_record(rec["path"], meta, rec["body"])
        _render_board_unlocked(board_dir, cfg, write_html=True, force_md=True)

    return {"action": "sync-md", "dry_run": dry_run, "changes": changes, "count": len(changes)}


def validate_board(board_dir: Path, today_str: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        cfg = load_config(board_dir)
    except KanbanError as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": []}

    col_map = get_column_map(cfg)
    h_map = get_horizon_map(cfg)
    prefix = cfg.get("id_prefix", "T")

    all_records: list[dict] = []
    for folder_name, archived in (("tasks", False), ("archive", True)):
        folder = board_dir / folder_name
        if not folder.exists():
            errors.append(f"Dossier manquant : {folder_name}/")
            continue
        for md_file in sorted(folder.glob("*.md")):
            try:
                meta, body = parse_task_markdown(md_file.read_text(encoding="utf-8"), md_file.name)
            except KanbanError as exc:
                errors.append(str(exc))
                continue
            all_records.append({"meta": meta, "body": body, "path": md_file, "filename": md_file.name, "archived": archived})

    seen_ids: dict[str, str] = {}
    by_id: dict[str, dict] = {}
    for rec in all_records:
        meta = rec["meta"]
        fname = rec["filename"]
        for req in REQUIRED_FIELDS:
            if not str(meta.get(req, "")).strip():
                errors.append(f"{fname} : champ obligatoire manquant '{req}'")
        tid = str(meta.get("id", "")).strip()
        if tid:
            if tid in seen_ids:
                errors.append(f"ID en double '{tid}' dans {seen_ids[tid]} et {fname}")
            else:
                seen_ids[tid] = fname
                by_id[tid] = rec
            if not fname.startswith(f"{tid}-") and fname != f"{tid}.md":
                warnings.append(f"{fname} : le nom de fichier ne commence pas par '{tid}-'")
            if extract_id_number(tid, prefix) is None:
                warnings.append(f"{fname} : format d'ID inhabituel '{tid}' (préfixe attendu '{prefix}-')")

        status = str(meta.get("status", ""))
        if status and status not in col_map:
            errors.append(f"{fname} : colonne inconnue '{status}'")
        prio = str(meta.get("priority", ""))
        if prio and prio not in PRIORITIES:
            errors.append(f"{fname} : priorité inconnue '{prio}'")
        horizon = str(meta.get("horizon", "") or "")
        if horizon and horizon not in h_map:
            errors.append(f"{fname} : horizon inconnu '{horizon}'")

        for ts_field in ("created_at", "updated_at", "claimed_at"):
            val = str(meta.get(ts_field, "") or "")
            if val:
                try:
                    parse_utc(val)
                except KanbanError as exc:
                    errors.append(f"{fname} : {exc}")

        s_date = None
        d_date = None
        for d_field in ("start_date", "due_date", "defer_until"):
            val = str(meta.get(d_field, "") or "")
            if val:
                try:
                    parsed = parse_date(val, d_field)
                    if d_field == "start_date":
                        s_date = parsed
                    elif d_field == "due_date":
                        d_date = parsed
                except KanbanError as exc:
                    errors.append(f"{fname} : {exc}")
        if s_date and d_date and s_date > d_date:
            errors.append(f"{fname} : start_date ({s_date}) > due_date ({d_date})")

        for raw_att in meta.get("attachments") or []:
            att_str = str(raw_att or "").strip().replace("\\", "/")
            if ".." in Path(att_str).parts or att_str.startswith("/") or re.match(r"^[A-Za-z]:", att_str):
                errors.append(f"{fname} : chemin de pièce jointe non autorisé '{raw_att}'")

    for rec in all_records:
        tid = str(rec["meta"].get("id", "")).strip() or rec["filename"]
        for dep in rec["meta"].get("depends_on") or []:
            dep_id = str(dep)
            if dep_id == tid:
                errors.append(f"{rec['filename']} ({tid}) : dépendance sur soi-même")
            elif dep_id not in by_id:
                errors.append(f"{rec['filename']} ({tid}) : dépendance introuvable '{dep_id}'")

    for cycle in detect_dependency_cycles(by_id):
        errors.append("Cycle de dépendances détecté : " + " -> ".join(cycle))

    drift, _ = check_kanban_md_drift(board_dir, cfg)
    if drift:
        warnings.append("KANBAN.md a été modifié manuellement (exécutez 'sync-md' ou 'render --force-md').")

    return {
        "valid": len(errors) == 0,
        "tasks_checked": len(all_records),
        "errors": errors,
        "warnings": warnings,
    }


def build_focus_report(cfg: dict, enriched: list[dict], today_str: str | None = None) -> dict:
    ref_date, _ = resolve_ref_datetime(today_str)
    soon_limit = ref_date + dt.timedelta(days=3)
    active = [t for t in enriched if not t["archived"] and not t["is_deferred"]]
    stats = compute_board_stats(cfg, enriched)

    to_review = [t for t in active if t["role"] == "review"]
    in_progress = [t for t in active if t["role"] == "in_progress"]
    urgent_due = []
    for t in active:
        if t["role"] == "done" or not t["due_date"]:
            continue
        with contextlib.suppress(KanbanError):
            d = parse_date(t["due_date"], "due_date")
            if d <= soon_limit:
                urgent_due.append(t)

    this_week = [
        t for t in active
        if t["horizon"] == "cette_semaine" and t["role"] in ("todo", "backlog") and not t["blocked"] and not t["implicitly_blocked"]
    ]
    ready_todo = [
        t for t in active
        if t["role"] == "todo" and not t["blocked"] and not t["implicitly_blocked"] and t["horizon"] != "cette_semaine"
    ]
    prio_rank = {"urgente": 0, "haute": 1, "normale": 2, "basse": 3}
    ready_todo.sort(key=lambda t: (prio_rank.get(t["priority"], 9), t["due_date"] or "9999-12-31", t["id"]))

    def slim(items: list[dict]) -> list[dict]:
        return [
            {
                "id": t["id"],
                "title": t["title"],
                "status": t["status"],
                "priority": t["priority"],
                "assignee": t["assignee"],
                "due_date": t["due_date"],
                "is_overdue": t["is_overdue"],
                "age_in_status_days": t["age_in_status_days"],
            }
            for t in items
        ]

    return {
        "today": ref_date.isoformat(),
        "wip_alerts": stats["wip_alerts"],
        "deferred_hidden": stats["deferred_count"],
        "to_review": slim(to_review),
        "overdue_or_soon": slim(urgent_due),
        "in_progress": slim(in_progress),
        "this_week_ready": slim(this_week),
        "next_ready_todo": slim(ready_todo[:5]),
    }


def build_triage_report(cfg: dict, enriched: list[dict], today_str: str | None = None) -> dict:
    ref_date, _ = resolve_ref_datetime(today_str)
    wake_limit = ref_date + dt.timedelta(days=7)
    active = [t for t in enriched if not t["archived"]]

    waking_soon = []
    for t in active:
        if t["is_deferred"] and t["defer_until"]:
            with contextlib.suppress(KanbanError):
                if parse_date(t["defer_until"], "defer_until") <= wake_limit:
                    waking_soon.append(t)

    def slim(items: list[dict]) -> list[dict]:
        return [
            {
                "id": t["id"],
                "title": t["title"],
                "status": t["status"],
                "priority": t["priority"],
                "age_in_status_days": t["age_in_status_days"],
                "blocked_reason": t["blocked_reason"],
                "unmet_dependencies": t["unmet_dependencies"],
                "due_date": t["due_date"],
                "defer_until": t["defer_until"],
            }
            for t in items
        ]

    stale = sorted([t for t in active if t["is_stale"]], key=lambda x: -x["age_in_status_days"])
    return {
        "today": ref_date.isoformat(),
        "ideas": slim([t for t in active if t["role"] == "backlog"]),
        "blocked": slim([t for t in active if t["blocked"]]),
        "implicitly_blocked": slim([t for t in active if t["implicitly_blocked"]]),
        "stale": slim(stale),
        "overdue": slim([t for t in active if t["is_overdue"]]),
        "waking_soon": slim(waking_soon),
    }


def filter_tasks(enriched: list[dict], args: argparse.Namespace) -> list[dict]:
    results = []
    for t in enriched:
        if not getattr(args, "include_archived", False) and t["archived"]:
            continue
        if not getattr(args, "include_deferred", False) and t["is_deferred"]:
            continue
        if getattr(args, "status", None) and t["status"] != args.status:
            continue
        if getattr(args, "priority", None) and t["priority"] != args.priority:
            continue
        if getattr(args, "assignee", None) and t["assignee"] != args.assignee:
            continue
        if getattr(args, "tag", None) and args.tag not in t["tags"]:
            continue
        if getattr(args, "category", None) and t["category"] != args.category:
            continue
        if getattr(args, "horizon", None) and t["horizon"] != args.horizon:
            continue
        if getattr(args, "blocked", False) and not (t["blocked"] or t["implicitly_blocked"]):
            continue
        if getattr(args, "overdue", False) and not t["is_overdue"]:
            continue
        if getattr(args, "stale", False) and not t["is_stale"]:
            continue
        if getattr(args, "search", None):
            q = args.search.lower()
            hay = " ".join([t["id"], t["title"], t["body"], t["category"], " ".join(t["tags"])]).lower()
            if q not in hay:
                continue
        results.append(t)
    return results


def _run_batch(board_dir: Path, payload: list[dict], today_str: str | None) -> dict:
    if not isinstance(payload, list):
        raise KanbanError("Le JSON de 'batch' doit être un tableau d'opérations")
    results: list[dict] = []
    for idx, item in enumerate(payload):
        if not isinstance(item, dict) or "op" not in item:
            raise KanbanError(f"Opération batch #{idx} invalide : 'op' manquant")
        op = str(item["op"]).strip()
        if op == "init":
            results.append(_op_init(board_dir, str(item.get("title", "Tableau Kanban")), bool(item.get("force", False))))
            continue
        cfg = load_config(board_dir)
        if op == "add":
            results.append(_op_add(board_dir, cfg, item))
        elif op == "edit":
            results.append(_op_edit(board_dir, cfg, item))
        elif op == "move":
            results.append(_op_move(board_dir, cfg, item))
        elif op == "claim":
            results.append(_op_claim(board_dir, cfg, item))
        elif op == "release":
            results.append(_op_release(board_dir, item))
        elif op == "block":
            results.append(_op_block(board_dir, item, blocked=True))
        elif op == "unblock":
            results.append(_op_block(board_dir, item, blocked=False))
        elif op == "check":
            results.append(_op_toggle_subtask(board_dir, item, checked=True))
        elif op == "uncheck":
            results.append(_op_toggle_subtask(board_dir, item, checked=False))
        elif op == "archive":
            results.append(_op_archive(board_dir, cfg, item))
        elif op == "delete":
            results.append(_op_delete(board_dir, cfg, item.get("id", ""), force=bool(item.get("force", False))))
        elif op == "config":
            results.append(_op_config(board_dir, cfg, item))
        elif op == "save_settings":
            results.append(_op_save_settings(board_dir, cfg, item))
        elif op in ("rename-category", "rename_category"):
            results.append(_op_rename_category(board_dir, cfg, item.get("old", ""), item.get("new", "")))
        elif op in ("rename-tag", "rename_tag"):
            results.append(_op_rename_tag(board_dir, cfg, item.get("old", ""), item.get("new", "")))
        elif op == "add_taxonomy":
            results.append(_op_add_taxonomy(board_dir, cfg, item.get("kind", ""), item.get("value", "")))
        else:
            raise KanbanError(f"Opération batch inconnue : {op!r}")

    cfg = load_config(board_dir)
    render_info = _render_board_unlocked(board_dir, cfg, write_html=True, force_md=False, today_str=today_str)
    return {"action": "batch", "operations": results, "count": len(results), "render": render_info}


def create_serve_httpd(
    board_dir: Path,
    port: int = 8765,
    today_str: str | None = None,
    allow_port_fallback: bool = True,
) -> tuple[ThreadingHTTPServer, str]:
    """Crée un serveur HTTP local (127.0.0.1 uniquement) pour l'édition interactive."""
    load_config(board_dir)
    csrf_token = secrets.token_urlsafe(24)

    def current_payload() -> dict:
        cfg = load_config(board_dir)
        active, archived = load_all_records(board_dir)
        enriched = enrich_records(active, archived, cfg, today_str=today_str)
        return build_html_payload(
            cfg,
            enriched,
            today_str=today_str,
            interactive=True,
            csrf_token=csrf_token,
        )

    class KanbanHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send_bytes(self, code: int, content_type: str, data: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, code: int, obj: dict) -> None:
            self._send_bytes(
                code,
                "application/json; charset=utf-8",
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            )

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path in ("/", "/kanban.html"):
                    cfg = load_config(board_dir)
                    active, archived = load_all_records(board_dir)
                    enriched = enrich_records(active, archived, cfg, today_str=today_str)
                    page = render_kanban_html(
                        cfg,
                        enriched,
                        today_str=today_str,
                        interactive=True,
                        csrf_token=csrf_token,
                    )
                    self._send_bytes(200, "text/html; charset=utf-8", page.encode("utf-8"))
                elif path == "/api/state":
                    self._send_json(200, current_payload())
                elif path.startswith("/attachments/"):
                    parts = [p for p in path.split("/") if p]
                    if len(parts) != 3 or not re.match(r"^[A-Za-z0-9_-]+$", parts[1]):
                        self._send_json(404, {"ok": False, "error": "Pièce jointe introuvable"})
                        return
                    tid_seg, raw_name = parts[1], parts[2]
                    ext = Path(raw_name).suffix.lower()
                    if ext not in SAFE_ATTACHMENT_EXTS:
                        self._send_json(403, {"ok": False, "error": "Type de fichier non autorisé"})
                        return
                    att_root = (board_dir / "attachments").resolve()
                    att_dir = (att_root / tid_seg).resolve()
                    if att_dir.parent != att_root or not att_dir.is_dir():
                        self._send_json(404, {"ok": False, "error": "Pièce jointe introuvable"})
                        return
                    try:
                        target = safe_task_path(att_dir, raw_name)
                    except KanbanError:
                        self._send_json(404, {"ok": False, "error": "Pièce jointe introuvable"})
                        return
                    if not target.is_file():
                        self._send_json(404, {"ok": False, "error": "Pièce jointe introuvable"})
                        return
                    data = target.read_bytes()
                    ctype, _ = mimetypes.guess_type(target.name)
                    if not ctype:
                        ctype = "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    if ext not in RASTER_ATTACHMENT_EXTS:
                        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send_json(404, {"ok": False, "error": "Ressource introuvable"})
            except KanbanError as exc:
                self._send_json(500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path not in ("/api/action", "/api/upload"):
                self._send_json(404, {"ok": False, "error": "Endpoint introuvable"})
                return

            origin = self.headers.get("Origin")
            if origin:
                host = (urlparse(origin).hostname or "").lower()
                if host not in ("127.0.0.1", "localhost"):
                    self._send_json(403, {"ok": False, "error": "Origine refusée"})
                    return

            token = self.headers.get("X-Kanban-Token", "")
            if not secrets.compare_digest(token, csrf_token):
                self._send_json(403, {"ok": False, "error": "Jeton CSRF invalide ou manquant"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"ok": False, "error": "Content-Length invalide"})
                return
            max_body = 8 * 1024 * 1024 if path == "/api/upload" else 1048576
            if length <= 0 or length > max_body:
                self._send_json(400, {"ok": False, "error": "Taille de requête invalide"})
                return

            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": f"JSON invalide : {exc}"})
                return

            if path == "/api/upload":
                if not isinstance(payload, dict):
                    self._send_json(400, {"ok": False, "error": "Payload JSON invalide"})
                    return
                tid = str(payload.get("id", "")).strip()
                fname = str(payload.get("filename", "")).strip()
                b64 = str(payload.get("content_base64", "")).strip()
                if not tid or not fname or not b64:
                    self._send_json(400, {"ok": False, "error": "Champs id, filename et content_base64 requis"})
                    return
                try:
                    raw_bytes = base64.b64decode(b64, validate=True)
                except Exception:
                    self._send_json(400, {"ok": False, "error": "Contenu base64 invalide"})
                    return
                try:
                    with BoardLock(board_dir):
                        cfg = load_config(board_dir)
                        res = _op_attach(board_dir, cfg, tid, filename=fname, raw_bytes=raw_bytes)
                        _render_board_unlocked(board_dir, cfg, write_html=True, today_str=today_str)
                        fresh = current_payload()
                    self._send_json(200, {"ok": True, "result": res, "state": fresh})
                except KanbanError as exc:
                    msg = str(exc)
                    code = 409 if "modifié manuellement" in msg else 400
                    self._send_json(code, {"ok": False, "error": msg})
                return

            if not isinstance(payload, dict) or not payload.get("op"):
                self._send_json(400, {"ok": False, "error": "Champ 'op' obligatoire"})
                return

            op = str(payload["op"]).strip()
            try:
                with BoardLock(board_dir):
                    cfg = load_config(board_dir)
                    rendered_already = False
                    if op == "add":
                        res = _op_add(board_dir, cfg, payload)
                    elif op == "edit":
                        res = _op_edit(board_dir, cfg, payload)
                    elif op == "save_card":
                        res = _op_save_card(board_dir, cfg, payload)
                    elif op == "move":
                        res = _op_move(board_dir, cfg, payload)
                    elif op == "claim":
                        res = _op_claim(board_dir, cfg, payload)
                    elif op == "release":
                        res = _op_release(board_dir, payload)
                    elif op == "block":
                        res = _op_block(board_dir, payload, blocked=True)
                    elif op == "unblock":
                        res = _op_block(board_dir, payload, blocked=False)
                    elif op == "check":
                        res = _op_toggle_subtask(board_dir, payload, checked=True)
                    elif op == "uncheck":
                        res = _op_toggle_subtask(board_dir, payload, checked=False)
                    elif op == "archive":
                        res = _op_archive(board_dir, cfg, payload)
                    elif op == "delete":
                        res = _op_delete(board_dir, cfg, payload.get("id", ""), force=bool(payload.get("force", False)))
                    elif op == "attach_ref":
                        res = _op_attach(board_dir, cfg, payload.get("id", ""), ref_only=payload.get("ref", ""))
                    elif op == "detach":
                        res = _op_detach(board_dir, cfg, payload.get("id", ""), payload.get("target", ""))
                    elif op == "save_settings":
                        res = _op_save_settings(board_dir, cfg, payload)
                    elif op in ("rename_category", "rename-category"):
                        res = _op_rename_category(board_dir, cfg, payload.get("old", ""), payload.get("new", ""))
                    elif op in ("rename_tag", "rename-tag"):
                        res = _op_rename_tag(board_dir, cfg, payload.get("old", ""), payload.get("new", ""))
                    elif op == "add_taxonomy":
                        res = _op_add_taxonomy(board_dir, cfg, payload.get("kind", ""), payload.get("value", ""))
                    elif op == "sync-md":
                        res = _op_sync_md(
                            board_dir,
                            cfg,
                            actor=str(payload.get("actor", "humain") or "humain"),
                            dry_run=False,
                        )
                        rendered_already = True
                    else:
                        raise KanbanError(f"Opération non supportée : {op!r}")

                    if not rendered_already:
                        _render_board_unlocked(board_dir, cfg, write_html=True, today_str=today_str)
                    fresh = current_payload()
                self._send_json(200, {"ok": True, "result": res, "state": fresh})
            except KanbanError as exc:
                msg = str(exc)
                code = 409 if "modifié manuellement" in msg else 400
                self._send_json(code, {"ok": False, "error": msg})

    class _KanbanHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = os.name != "nt"

    candidates = [port]
    if allow_port_fallback and port > 0:
        candidates.extend(range(port + 1, min(port + 20, 65535)))
        candidates.append(0)

    server: ThreadingHTTPServer | None = None
    last_err: OSError | None = None
    for cand in candidates:
        try:
            server = _KanbanHTTPServer(("127.0.0.1", cand), KanbanHandler)
            break
        except OSError as exc:
            last_err = exc
    if server is None:
        raise KanbanError(f"Impossible de démarrer le serveur local sur le port {port} : {last_err}")
    return server, csrf_token


def print_human(data: dict) -> None:
    if "valid" in data:
        status = "OK" if data["valid"] else "ECHEC"
        print(f"Validation [{status}] - {data['tasks_checked']} carte(s) vérifiée(s)")
        for err in data["errors"]:
            print(f"  ERREUR : {err}")
        for warn in data["warnings"]:
            print(f"  ALERTE : {warn}")
        return
    if "tasks" in data and isinstance(data["tasks"], list):
        print(f"{len(data['tasks'])} carte(s) :")
        for t in data["tasks"]:
            flags = []
            if t.get("blocked"):
                flags.append("BLOQUÉ")
            if t.get("implicitly_blocked"):
                flags.append("ATTENTE:" + ",".join(t["unmet_dependencies"]))
            if t.get("is_overdue"):
                flags.append("RETARD")
            if t.get("is_stale"):
                flags.append("STAGNANTE")
            flag_str = f" [{' | '.join(flags)}]" if flags else ""
            print(f"  - {t['id']} ({t['status']}) [{t['priority']}] {t['title']}{flag_str}")
        return
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kanban.py", description="Moteur Kanban Markdown local (0 dépendance)")
    parser.add_argument("--dir", default="./kanban", help="Dossier du tableau Kanban (défaut: ./kanban)")
    parser.add_argument("--json", action="store_true", help="Sortie JSON structurée")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--title", default="Tableau Kanban")
    p_init.add_argument("--force", action="store_true")

    p_add = sub.add_parser("add")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--status", default="idees")
    p_add.add_argument("--priority", default="normale")
    p_add.add_argument("--tags", default="")
    p_add.add_argument("--assignee", default="")
    p_add.add_argument("--category", default="")
    p_add.add_argument("--horizon", default="")
    p_add.add_argument("--start-date", default="")
    p_add.add_argument("--due-date", default="")
    p_add.add_argument("--defer-until", default="")
    p_add.add_argument("--depends-on", default="")
    p_add.add_argument("--subtask", action="append", default=[])
    p_add.add_argument("--body", default="")
    p_add.add_argument("--actor", default="humain")

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("id")
    p_edit.add_argument("--title")
    p_edit.add_argument("--priority")
    p_edit.add_argument("--tags")
    p_edit.add_argument("--add-tags")
    p_edit.add_argument("--assignee")
    p_edit.add_argument("--category")
    p_edit.add_argument("--horizon")
    p_edit.add_argument("--start-date")
    p_edit.add_argument("--due-date")
    p_edit.add_argument("--defer-until")
    p_edit.add_argument("--depends-on")
    p_edit.add_argument("--append-note")

    p_move = sub.add_parser("move")
    p_move.add_argument("id")
    p_move.add_argument("status")
    p_move.add_argument("--actor", default="humain")
    p_move.add_argument("--note", default="")
    p_move.add_argument("--as-agent", action="store_true")
    p_move.add_argument("--force-done", action="store_true")

    p_claim = sub.add_parser("claim")
    p_claim.add_argument("id")
    p_claim.add_argument("--by", required=True)
    p_claim.add_argument("--start", action="store_true")
    p_claim.add_argument("--force", action="store_true")

    p_rel = sub.add_parser("release")
    p_rel.add_argument("id")

    p_block = sub.add_parser("block")
    p_block.add_argument("id")
    p_block.add_argument("--reason", required=True)

    p_unblock = sub.add_parser("unblock")
    p_unblock.add_argument("id")

    p_chk = sub.add_parser("check")
    p_chk.add_argument("id")
    p_chk.add_argument("target")

    p_unchk = sub.add_parser("uncheck")
    p_unchk.add_argument("id")
    p_unchk.add_argument("target")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status")
    p_list.add_argument("--priority")
    p_list.add_argument("--assignee")
    p_list.add_argument("--tag")
    p_list.add_argument("--category")
    p_list.add_argument("--horizon")
    p_list.add_argument("--blocked", action="store_true")
    p_list.add_argument("--overdue", action="store_true")
    p_list.add_argument("--stale", action="store_true")
    p_list.add_argument("--include-deferred", action="store_true")
    p_list.add_argument("--include-archived", action="store_true")
    p_list.add_argument("--search")
    p_list.add_argument("--today")

    p_show = sub.add_parser("show")
    p_show.add_argument("id")
    p_show.add_argument("--today")

    p_focus = sub.add_parser("focus")
    p_focus.add_argument("--today")

    p_triage = sub.add_parser("triage")
    p_triage.add_argument("--today")

    p_arch = sub.add_parser("archive")
    p_arch.add_argument("--id")
    p_arch.add_argument("--done-older-than", type=int)
    p_arch.add_argument("--all-done", action="store_true")
    p_arch.add_argument("--force", action="store_true")
    p_arch.add_argument("--today")

    p_del = sub.add_parser("delete")
    p_del.add_argument("id")
    p_del.add_argument("--force", action="store_true")

    p_att = sub.add_parser("attach")
    p_att.add_argument("id")
    p_att_grp = p_att.add_mutually_exclusive_group(required=True)
    p_att_grp.add_argument("--file")
    p_att_grp.add_argument("--ref")

    p_det = sub.add_parser("detach")
    p_det.add_argument("id")
    p_det.add_argument("target")

    p_render = sub.add_parser("render")
    p_render.add_argument("--no-html", action="store_true")
    p_render.add_argument("--force-md", action="store_true")
    p_render.add_argument("--today")

    p_sync = sub.add_parser("sync-md")
    p_sync.add_argument("--actor", default="humain")
    p_sync.add_argument("--dry-run", action="store_true")

    p_stats = sub.add_parser("stats")
    p_stats.add_argument("--today")

    p_val = sub.add_parser("validate")
    p_val.add_argument("--today")

    p_cfg = sub.add_parser("config")
    p_cfg.add_argument("--title")
    p_cfg.add_argument("--stale-days", type=int)
    p_cfg.add_argument("--set-wip", action="append", default=[])
    p_cfg.add_argument("--obsidian")
    p_cfg.add_argument("--category-label")
    p_cfg.add_argument("--tag-label")
    p_cfg.add_argument("--rename-column", action="append", default=[])
    p_cfg.add_argument("--rename-horizon", action="append", default=[])

    p_rcat = sub.add_parser("rename-category")
    p_rcat.add_argument("old_name")
    p_rcat.add_argument("new_name", nargs="?", default="")

    p_rtag = sub.add_parser("rename-tag")
    p_rtag.add_argument("old_tag")
    p_rtag.add_argument("new_tag", nargs="?", default="")

    p_batch = sub.add_parser("batch")
    p_batch.add_argument("--stdin", action="store_true")
    p_batch.add_argument("--input-file")
    p_batch.add_argument("--today")

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--today")
    p_serve.add_argument("--open", dest="open_browser", action="store_true")
    p_serve.add_argument("--strict-port", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    board_dir = Path(args.dir).resolve()

    try:
        cmd = args.command
        if cmd == "init":
            with BoardLock(board_dir):
                res = _op_init(board_dir, args.title, args.force)
        elif cmd == "add":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_add(
                    board_dir,
                    cfg,
                    {
                        "title": args.title,
                        "status": args.status,
                        "priority": args.priority,
                        "tags": args.tags,
                        "assignee": args.assignee,
                        "category": args.category,
                        "horizon": args.horizon,
                        "start_date": args.start_date,
                        "due_date": args.due_date,
                        "defer_until": args.defer_until,
                        "depends_on": args.depends_on,
                        "subtasks": args.subtask,
                        "body": args.body,
                        "actor": args.actor,
                    },
                )
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "edit":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_edit(
                    board_dir,
                    cfg,
                    {
                        "id": args.id,
                        "title": args.title,
                        "priority": args.priority,
                        "tags": args.tags,
                        "add_tags": args.add_tags,
                        "assignee": args.assignee,
                        "category": args.category,
                        "horizon": args.horizon,
                        "start_date": args.start_date,
                        "due_date": args.due_date,
                        "defer_until": args.defer_until,
                        "depends_on": args.depends_on,
                        "append_note": args.append_note,
                    },
                )
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "move":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_move(
                    board_dir,
                    cfg,
                    {
                        "id": args.id,
                        "status": args.status,
                        "actor": args.actor,
                        "note": args.note,
                        "as_agent": args.as_agent,
                        "force_done": args.force_done,
                    },
                )
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "claim":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_claim(board_dir, cfg, {"id": args.id, "by": args.by, "start": args.start, "force": args.force})
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "release":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_release(board_dir, {"id": args.id})
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "block":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_block(board_dir, {"id": args.id, "reason": args.reason}, blocked=True)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "unblock":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_block(board_dir, {"id": args.id}, blocked=False)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd in ("check", "uncheck"):
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_toggle_subtask(board_dir, {"id": args.id, "target": args.target}, checked=(cmd == "check"))
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "archive":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_archive(
                    board_dir,
                    cfg,
                    {
                        "id": args.id,
                        "done_older_than": args.done_older_than,
                        "all_done": args.all_done,
                        "force": args.force,
                        "today": args.today,
                    },
                )
                res["render"] = _render_board_unlocked(board_dir, cfg, today_str=args.today)
        elif cmd == "delete":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_delete(board_dir, cfg, args.id, force=args.force)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "attach":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_attach(board_dir, cfg, args.id, source_path=args.file, ref_only=args.ref)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "detach":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_detach(board_dir, cfg, args.id, args.target)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "render":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _render_board_unlocked(
                    board_dir,
                    cfg,
                    write_html=not args.no_html,
                    force_md=args.force_md,
                    today_str=args.today,
                )
        elif cmd == "sync-md":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_sync_md(board_dir, cfg, actor=args.actor, dry_run=args.dry_run)
        elif cmd == "config":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_config(
                    board_dir,
                    cfg,
                    {
                        "title": args.title,
                        "stale_days": args.stale_days,
                        "set_wip": args.set_wip,
                        "obsidian": args.obsidian,
                        "category_label": args.category_label,
                        "tag_label": args.tag_label,
                        "rename_column": args.rename_column,
                        "rename_horizon": args.rename_horizon,
                    },
                )
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "rename-category":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_rename_category(board_dir, cfg, args.old_name, args.new_name)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "rename-tag":
            with BoardLock(board_dir):
                cfg = load_config(board_dir)
                res = _op_rename_tag(board_dir, cfg, args.old_tag, args.new_tag)
                res["render"] = _render_board_unlocked(board_dir, cfg)
        elif cmd == "batch":
            if args.input_file:
                raw_json = Path(args.input_file).read_text(encoding="utf-8")
            elif args.stdin:
                raw_json = sys.stdin.read()
            else:
                raise KanbanError("Précisez --stdin ou --input-file pour la commande batch")
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                raise KanbanError(f"JSON batch invalide : {exc}") from exc
            with BoardLock(board_dir):
                res = _run_batch(board_dir, payload, today_str=args.today)
        elif cmd == "list":
            cfg = load_config(board_dir)
            active, archived = load_all_records(board_dir)
            enriched = enrich_records(active, archived, cfg, today_str=args.today)
            filtered = filter_tasks(enriched, args)
            res = {
                "count": len(filtered),
                "tasks": [
                    {k: v for k, v in t.items() if k not in ("meta", "path")}
                    for t in filtered
                ],
            }
        elif cmd == "show":
            cfg = load_config(board_dir)
            active, archived = load_all_records(board_dir)
            enriched = enrich_records(active, archived, cfg, today_str=args.today)
            rec = find_task_record(enriched, args.id)
            res = {k: v for k, v in rec.items() if k not in ("meta", "path")}
            res["history"] = rec["meta"].get("history", [])
        elif cmd == "focus":
            cfg = load_config(board_dir)
            active, archived = load_all_records(board_dir)
            enriched = enrich_records(active, archived, cfg, today_str=args.today)
            res = build_focus_report(cfg, enriched, today_str=args.today)
        elif cmd == "triage":
            cfg = load_config(board_dir)
            active, archived = load_all_records(board_dir)
            enriched = enrich_records(active, archived, cfg, today_str=args.today)
            res = build_triage_report(cfg, enriched, today_str=args.today)
        elif cmd == "stats":
            cfg = load_config(board_dir)
            active, archived = load_all_records(board_dir)
            enriched = enrich_records(active, archived, cfg, today_str=args.today)
            res = compute_board_stats(cfg, enriched)
        elif cmd == "validate":
            res = validate_board(board_dir, today_str=args.today)
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print_human(res)
            return 0 if res["valid"] else 1
        elif cmd == "serve":
            httpd, _ = create_serve_httpd(
                board_dir,
                port=args.port,
                today_str=args.today,
                allow_port_fallback=not args.strict_port,
            )
            actual_port = httpd.server_address[1]
            url = f"http://127.0.0.1:{actual_port}/kanban.html"
            if args.port and actual_port != args.port:
                print(f"Port {args.port} déjà utilisé ; bascule automatique sur le port {actual_port}.", flush=True)
            print(f"Serveur Kanban interactif prêt sur {url}", flush=True)
            if args.open_browser:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                httpd.server_close()
            return 0
        else:
            raise KanbanError(f"Commande non gérée : {cmd}")

        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print_human(res)
        return 0
    except KanbanError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
