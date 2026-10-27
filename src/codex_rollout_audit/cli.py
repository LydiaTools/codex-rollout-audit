from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_METADATA_LINE = 64 * 1024
GIB = 1024**3
MIB = 1024**2


@dataclass
class Rollout:
    path: Path
    size: int
    thread_id: str | None = None
    session_id: str | None = None
    parent_thread_id: str | None = None
    forked_from_id: str | None = None
    metadata_state: str = "unread"


@dataclass
class Family:
    key: str
    rollouts: list[Rollout] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(item.size for item in self.rollouts)

    @property
    def child_size(self) -> int:
        return sum(item.size for item in self.rollouts if item.parent_thread_id)

    @property
    def fork_size(self) -> int:
        return sum(item.size for item in self.rollouts if item.forked_from_id)

    @property
    def threads(self) -> int:
        return len({item.thread_id for item in self.rollouts if item.thread_id})


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _string_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    return None


def _metadata_from_line(line: bytes) -> dict[str, str | None] | None:
    try:
        entry = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(entry, dict) or entry.get("type") != "session_meta":
        return None
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = payload
    return {
        "thread_id": _string_id(meta.get("id") or meta.get("thread_id") or meta.get("threadId")),
        "session_id": _string_id(meta.get("session_id") or meta.get("sessionId")),
        "parent_thread_id": _string_id(meta.get("parent_thread_id") or meta.get("parentThreadId")),
        "forked_from_id": _string_id(meta.get("forked_from_id") or meta.get("forkedFromId")),
    }


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _walk_files(root: Path):
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(name for name in dirs if not (current_path / name).is_symlink())
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink():
                continue
            if name.endswith(".jsonl") or name.endswith(".jsonl.zst"):
                yield path


def scan(root: Path) -> tuple[list[Rollout], dict[str, int]]:
    rollouts: list[Rollout] = []
    counts = {
        "files_seen": 0,
        "plain_jsonl": 0,
        "compressed_jsonl": 0,
        "metadata_read": 0,
        "metadata_unavailable": 0,
        "read_errors": 0,
    }
    for path in _walk_files(root):
        counts["files_seen"] += 1
        try:
            size = path.stat().st_size
        except OSError:
            counts["read_errors"] += 1
            continue
        item = Rollout(path=path, size=size)
        if path.name.endswith(".jsonl.zst"):
            item.metadata_state = "compressed"
            counts["compressed_jsonl"] += 1
        else:
            counts["plain_jsonl"] += 1
            try:
                with path.open("rb") as stream:
                    line = stream.readline(MAX_METADATA_LINE + 1)
                metadata = _metadata_from_line(line) if len(line) <= MAX_METADATA_LINE else None
                if metadata is None:
                    item.metadata_state = "unavailable"
                    counts["metadata_unavailable"] += 1
                else:
                    item.thread_id = metadata["thread_id"]
                    item.session_id = metadata["session_id"] or item.thread_id
                    item.parent_thread_id = metadata["parent_thread_id"]
                    item.forked_from_id = metadata["forked_from_id"]
                    item.metadata_state = "read"
                    counts["metadata_read"] += 1
            except OSError:
                item.metadata_state = "unavailable"
                counts["read_errors"] += 1
                counts["metadata_unavailable"] += 1
        rollouts.append(item)
    return rollouts, counts


def group_families(rollouts: list[Rollout]) -> tuple[list[Family], int]:
    uf = UnionFind()
    records_by_thread: dict[str, list[Rollout]] = {}
    unresolved_links = 0
    for item in rollouts:
        if not item.thread_id:
            continue
        thread_node = "thread:" + item.thread_id
        uf.find(thread_node)
        records_by_thread.setdefault(item.thread_id, []).append(item)
        if item.session_id:
            uf.union(thread_node, "session:" + item.session_id)

    for item in rollouts:
        if not item.thread_id:
            continue
        for relation in (item.parent_thread_id, item.forked_from_id):
            if not relation:
                continue
            if relation in records_by_thread:
                uf.union("thread:" + item.thread_id, "thread:" + relation)
            else:
                unresolved_links += 1

    members_by_component: dict[str, list[Rollout]] = {}
    for item in rollouts:
        if item.thread_id:
            component = uf.find("thread:" + item.thread_id)
            members_by_component.setdefault(component, []).append(item)

    grouped: dict[str, Family] = {}
    for members in members_by_component.values():
        candidates = sorted(
            item.thread_id for item in members
            if item.thread_id and not item.parent_thread_id and not item.forked_from_id
        )
        label_source = candidates[0] if candidates else min(item.thread_id for item in members if item.thread_id)
        key = "family-" + _stable_hash(label_source)
        grouped[key] = Family(key=key, rollouts=members)

    for item in rollouts:
        if item.thread_id:
            continue
        key = "unlinked-" + _stable_hash(str(item.path))
        grouped[key] = Family(key=key, rollouts=[item])

    return sorted(grouped.values(), key=lambda family: (-family.size, family.key)), unresolved_links


def human_size(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.2f} GiB"
    if value >= MIB:
        return f"{value / MIB:.1f} MiB"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value} B"


def display_root(root: Path) -> str:
    if root == (Path.home() / ".codex" / "sessions").resolve():
        return "~/.codex/sessions"
    return root.name or "."


def build_report(root: Path, top: int, show_paths: bool) -> dict[str, Any]:
    rollouts, counts = scan(root)
    families, unresolved = group_families(rollouts)
    total = sum(item.size for item in rollouts)
    compressed = sum(item.size for item in rollouts if item.metadata_state == "compressed")
    metadata_unavailable = sum(item.size for item in rollouts if item.metadata_state == "unavailable")
    rows = []
    for family in families[:top]:
        rows.append({
            "family": family.key,
            "rollouts": len(family.rollouts),
            "threads": family.threads,
            "total_bytes": family.size,
            "subagent_bytes": family.child_size,
            "fork_bytes": family.fork_size,
            "unlinked_bytes": sum(r.size for r in family.rollouts if r.metadata_state != "read"),
            **({"files": [str(r.path.relative_to(root)) for r in family.rollouts]} if show_paths else {}),
        })
    return {
        "root": display_root(root),
        "total_bytes": total,
        "family_count": len(families),
        "compressed_unlinked_bytes": compressed,
        "metadata_unavailable_bytes": metadata_unavailable,
        "unresolved_relationships": unresolved,
        "counts": counts,
        "families_shown": rows,
    }


def print_text(report: dict[str, Any]) -> None:
    counts = report["counts"]
    print(f"Codex Rollout Audit — {report['root']}")
    print(f"Rollout files: {counts['files_seen']} | families: {report['family_count']} | total: {human_size(report['total_bytes'])}")
    print(f"Metadata read: {counts['metadata_read']} | compressed: {human_size(report['compressed_unlinked_bytes'])} | unavailable: {human_size(report['metadata_unavailable_bytes'])}")
    if report["unresolved_relationships"]:
        print(f"Relationships whose parent file was not found: {report['unresolved_relationships']}")
    print()
    print(f"{'FAMILY':<19} {'FILES':>5} {'THREADS':>7} {'TOTAL':>12} {'SUBAGENT':>12} {'FORKED':>12}")
    for row in report["families_shown"]:
        print(f"{row['family']:<19} {row['rollouts']:>5} {row['threads']:>7} {human_size(row['total_bytes']):>12} {human_size(row['subagent_bytes']):>12} {human_size(row['fork_bytes']):>12}")
        for filename in row.get("files", []):
            print(f"  {filename}")
    if not report["families_shown"]:
        print("No rollout files found.")
    print("\nConversation text was not read. This command did not modify any files.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="codex-rollout-audit",
        description="Read-only disk-usage report for Codex rollout files and their recorded thread relationships.",
    )
    parser.add_argument("--root", type=Path, default=Path.home() / ".codex" / "sessions", help="Codex sessions directory (default: ~/.codex/sessions)")
    parser.add_argument("--top", type=int, default=20, help="number of largest families to show (default: 20)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--show-paths", action="store_true", help="include relative rollout paths in output")
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("--top must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"Error: sessions directory not found: {display_root(root)}", file=sys.stderr)
        return 2
    report = build_report(root, args.top, args.show_paths)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_text(report)
    return 0
