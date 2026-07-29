"""Work Folder mutation store — governed via GovernedKernel.

WorkFolderStore wraps a kernel binding and routes all work-folder mutations
through GovernedKernel.mutate (CAS → policy → VFS → ledger → manifest → git commit),
following the composition pattern established by MemoryStore and WikiStore.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict

from katana_kernel import CASRejectionError, IdempotencyConflictError, head_sha
from katana_kernel.kernel import GovernedKernel
from katana_kernel.policy import DomainPolicy, PolicyViolationError
from katana_work_folder_mcp import artifacts as _art
from katana_work_folder_mcp import reindex as _reindex
from katana_work_folder_mcp import verify as _ver
from katana_work_folder_mcp.brief import BRIEF_NAME, BriefError, parse_brief, render_brief
from katana_work_folder_mcp.lifecycle import (
    RESUME_BLOCKED_CONTRACT,
    RESUME_PROCEED_CONTRACT,
    SAVE_CONTRACT,
    require_folder_id,
)


def _wf_policy() -> DomainPolicy:
    def _invariants(domain, op, args):
        if op == "wf_create":
            if not args.get("topic"):
                raise PolicyViolationError("topic is required for wf_create")
        elif op in ("wf_save", "wf_resume"):
            if not args.get("folder_id"):
                raise PolicyViolationError("folder_id is required")
        elif op == "wf_append_progress":
            required = (
                "folder_id",
                "entry",
                "source_session_id",
                "idempotency_key",
                "request_fingerprint",
            )
            if any(not args.get(key) for key in required):
                raise PolicyViolationError(
                    "folder_id, entry, source_session_id, idempotency_key and "
                    "request_fingerprint are required"
                )
        elif op == "fs_create":
            if not args.get("folder_id") or not args.get("filename"):
                raise PolicyViolationError(
                    "folder_id and filename are required for fs_create"
                )
            if not args.get("content"):
                raise PolicyViolationError("content is required for fs_create")
        elif op == "fs_write":
            if not args.get("folder_id") or not args.get("filename"):
                raise PolicyViolationError(
                    "folder_id and filename are required for fs_write"
                )
            if not args.get("content"):
                raise PolicyViolationError("content is required for fs_write")
        elif op == "fs_edit":
            if not args.get("folder_id") or not args.get("filename"):
                raise PolicyViolationError(
                    "folder_id and filename are required for fs_edit"
                )
            if not args.get("old_string"):
                raise PolicyViolationError("old_string is required for fs_edit")
        elif op == "fs_copy":
            required = (
                "source_folder_id",
                "source_filename",
                "dest_folder_id",
                "dest_filename",
            )
            if any(not args.get(key) for key in required):
                raise PolicyViolationError(
                    "source/dest folder IDs and filenames are required for fs_copy"
                )
        elif op == "fs_rename":
            required = (
                "source_folder_id",
                "source_filename",
                "dest_folder_id",
                "dest_filename",
            )
            if any(not args.get(key) for key in required):
                raise PolicyViolationError(
                    "source/dest folder IDs and filenames are required for fs_rename"
                )
        elif op in ("fs_delete", "delete"):
            if not args.get("folder_id") or not args.get("filename"):
                raise PolicyViolationError(
                    "folder_id and filename are required for fs_delete"
                )
        elif op == "fs_batch":
            if not args.get("operations"):
                raise PolicyViolationError("operations is required for fs_batch")

    return DomainPolicy(
        domain="work-folder",
        allowed_ops={"wf_create", "wf_save", "wf_resume", "wf_reindex",
            "wf_append_progress",
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename",
            "fs_delete", "fs_batch", "delete"},
        invariants=[_invariants],
    )


def _extract_field(md: str, label: str) -> str:
    m = re.search(rf"\*\*{label}:\*\*\s*(.+)", md)
    return m.group(1).strip() if m else ""


def _as_iso_str(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _scan_brief_ids(vfs) -> set[str]:
    ids: set[str] = set()
    for p in vfs.ls("**/_brief.md"):
        try:
            text = vfs.read_text(p)
            r = parse_brief(text)
            pid = r["frontmatter"].get("id")
            if pid:
                ids.add(pid)
        except Exception:
            pass
    return ids


def _append_error(
    code: str,
    message: str,
    *,
    folder_id: str | None = None,
    source_session_id: str | None = None,
    retryable: bool = False,
    commit: str | None = None,
) -> dict:
    result = {
        "ok": False,
        "code": code,
        "message": message,
        "retryable": retryable,
        "filename": "progress.md",
    }
    if folder_id is not None:
        result["folder_id"] = folder_id
    if source_session_id is not None:
        result["source_session_id"] = source_session_id
    if commit is not None:
        result["commit"] = commit
    return result


def _append_fingerprint(
    folder_id: str,
    entry: str,
    source_session_id: str,
) -> str:
    canonical = json.dumps(
        {
            "entry": entry,
            "folder_id": folder_id,
            "source_session_id": source_session_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _table_cell(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )


def _render_index_snapshot(binding) -> tuple[str, int, int, list[str]]:
    """从当前 VFS 状态机械生成 flat INDEX 内容。"""
    entries: list[dict] = []
    errors: list[str] = []
    for brief_path in binding.vfs.ls(f"wf-*/{BRIEF_NAME}"):
        folder_id = brief_path.split("/", 1)[0]
        try:
            parsed = parse_brief(binding.vfs.read_text(brief_path))
            brief_id = parsed["frontmatter"].get("id")
            if brief_id != folder_id:
                errors.append(
                    f"{folder_id}/{BRIEF_NAME}: id mismatch "
                    f"({brief_id} != {folder_id})"
                )
                continue
            entries.append(
                {
                    "folder_id": folder_id,
                    "fm": parsed["frontmatter"],
                    "goal": parsed["goal"],
                }
            )
        except BriefError as exc:
            errors.append(f"{brief_path}: {exc}")
        except Exception as exc:  # noqa: BLE001 - reindex skips corrupt peers
            errors.append(f"{brief_path}: {exc}")

    brief_folders = {entry["folder_id"] for entry in entries}
    progress_folders = {
        path.split("/", 1)[0]
        for path in binding.vfs.ls("wf-*/progress.md")
    }
    skipped = len(progress_folders - brief_folders)
    return _reindex.render_index(entries), len(entries), skipped, errors


class WorkFolderStore:
    def __init__(self, kernel: GovernedKernel):
        self._kernel = kernel
        self._binding = kernel.get_binding("work-folder")
        self._append_lock = threading.RLock()

    def _call_mutate(
        self,
        op: str,
        args: dict,
        write_fn,
        expected_base_sha: str | None,
        commit_msg: str,
        *,
        idempotency_key: str | None = None,
        idempotency_payload: dict | None = None,
    ) -> dict:
        return self._kernel.mutate(
            "work-folder", op, args,
            expected_base_sha=expected_base_sha,
            write_fn=write_fn,
            commit_msg=commit_msg,
            idempotency_key=idempotency_key,
            idempotency_payload=(
                idempotency_payload if idempotency_key is not None else None
            ),
        )

    def _folder_path(self, folder_id: str) -> str:
        """O(1) 校验 flat folder identity，并返回 repo-relative 目录名。"""
        folder_id = require_folder_id(folder_id)
        if not self._binding.vfs.exists(folder_id):
            raise FileNotFoundError(f"work-folder 不存在: {folder_id}")
        brief_path = f"{folder_id}/{BRIEF_NAME}"
        if not self._binding.vfs.exists(brief_path):
            raise ValueError(f"work-folder 缺少 {BRIEF_NAME}: {folder_id}")
        parsed = parse_brief(self._binding.vfs.read_text(brief_path))
        brief_id = parsed["frontmatter"].get("id")
        if brief_id != folder_id:
            raise ValueError(
                f"work-folder identity mismatch: {brief_id} != {folder_id}"
            )
        return folder_id

    def create(
        self,
        topic: str,
        now_fn,
        expected_base_sha: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        now = now_fn()
        args = {
            "topic": topic,
            "now_str": now.strftime("%Y-%m-%d %H:%M"),
            "now_date": now.strftime("%Y-%m-%d"),
        }

        def _write(binding, args):
            topic = args["topic"]
            now_str = args["now_str"]
            now_date = args["now_date"]

            existing_ids = _scan_brief_ids(binding.vfs)
            wf_id = binding.ledger.gen_id(existing_ids)
            while binding.vfs.exists(wf_id):
                existing_ids.add(wf_id)
                wf_id = binding.ledger.gen_id(existing_ids)
            folder = wf_id

            binding.vfs.mkdir(folder)
            changed: list[str] = []

            progress = _art.render_progress_skeleton(
                goal=topic, status="brainstorming", phase="", now=now_str,
            )
            binding.vfs.write(f"{folder}/progress.md", progress)
            changed.append(f"{folder}/progress.md")

            context = _art.render_context_skeleton(now=now_str)
            binding.vfs.write(f"{folder}/context.md", context)
            changed.append(f"{folder}/context.md")

            brief = render_brief(
                id=wf_id, title=topic, status="active",
                created=now_date, updated=now_date,
                goal=topic, summary="",
            )
            binding.vfs.write(f"{folder}/{BRIEF_NAME}", brief)
            changed.append(f"{folder}/{BRIEF_NAME}")
            index_md, _, _, _ = _render_index_snapshot(binding)
            binding.vfs.write("INDEX.md", index_md)
            changed.append("INDEX.md")

            return {
                "created": True,
                "folder_id": wf_id,
                "id": wf_id,
                "seeded": ["progress.md", "context.md", BRIEF_NAME],
                "drafting": SAVE_CONTRACT,
                "changed_paths": changed,
            }

        return self._call_mutate(
            "wf_create",
            args,
            _write,
            expected_base_sha,
            "work-folder: create",
            idempotency_key=idempotency_key,
            idempotency_payload={"topic": topic},
        )

    def save(self, folder_id: str, now_fn,
             summary: str = "checkpoint",
             context_snapshot: str | None = None,
             resume_fields: dict | None = None,
             golden_order_additions: str | None = None,
             findings_addition: str | None = None,
             expected_base_sha: str | None = None,
             idempotency_key: str | None = None) -> dict:
        now = now_fn()
        folder_id = require_folder_id(folder_id)
        args = {
            "folder_id": folder_id,
            "now_hm": now.strftime("%H:%M:%S"),
            "now_str": now.strftime("%Y-%m-%d %H:%M"),
            "now_date": now.strftime("%Y-%m-%d"),
            "summary": summary,
            "context_snapshot": context_snapshot,
            "resume_fields": resume_fields,
            "golden_order_additions": golden_order_additions,
            "findings_addition": findings_addition,
        }

        def _write(binding, args):
            folder = self._folder_path(args["folder_id"])

            changed: list[str] = []
            now_str = args["now_str"]
            now_date = args["now_date"]
            now_hm = args["now_hm"]

            if not binding.vfs.exists(f"{folder}/progress.md"):
                goal = ""
                rf = args.get("resume_fields") or {}
                if rf:
                    goal = rf.get("goal", "")
                progress = _art.render_progress_skeleton(
                    goal=goal or args["summary"], status="brainstorming",
                    phase="", now=now_str,
                )
                binding.vfs.write(f"{folder}/progress.md", progress)
                changed.append(f"{folder}/progress.md")
            if not binding.vfs.exists(f"{folder}/context.md"):
                context = _art.render_context_skeleton(now=now_str)
                binding.vfs.write(f"{folder}/context.md", context)
                changed.append(f"{folder}/context.md")

            if args["context_snapshot"] is not None:
                binding.vfs.write(f"{folder}/context.md", args["context_snapshot"])
                if f"{folder}/context.md" not in changed:
                    changed.append(f"{folder}/context.md")

            if args["golden_order_additions"]:
                existing = ""
                if binding.vfs.exists(f"{folder}/golden-order.md"):
                    existing = binding.vfs.read_text(f"{folder}/golden-order.md")
                binding.vfs.write(
                    f"{folder}/golden-order.md",
                    existing + args["golden_order_additions"],
                )
                if f"{folder}/golden-order.md" not in changed:
                    changed.append(f"{folder}/golden-order.md")

            if args["findings_addition"]:
                existing = ""
                if binding.vfs.exists(f"{folder}/findings.md"):
                    existing = binding.vfs.read_text(f"{folder}/findings.md")
                binding.vfs.write(
                    f"{folder}/findings.md",
                    existing + args["findings_addition"],
                )
                if f"{folder}/findings.md" not in changed:
                    changed.append(f"{folder}/findings.md")

            progress = binding.vfs.read_text(f"{folder}/progress.md")
            row = _art.changelog_row(now_hm, "checkpoint", args["summary"])
            updated_progress = _art.insert_changelog_row(progress, row)
            if updated_progress != progress:
                binding.vfs.write(f"{folder}/progress.md", updated_progress)
                if f"{folder}/progress.md" not in changed:
                    changed.append(f"{folder}/progress.md")

            rf = args.get("resume_fields") or {}
            if rf:
                fields = dict(rf)
            else:
                progress_md = binding.vfs.read_text(f"{folder}/progress.md")
                fields = {
                    "goal": _extract_field(progress_md, "Goal"),
                    "status": _extract_field(progress_md, "Status"),
                    "phase": _extract_field(progress_md, "Phase"),
                    "folder_id": folder_id,
                    "key_context": "",
                    "now": now_str,
                }
            fields.setdefault("goal", "")
            fields.setdefault("status", "")
            fields.setdefault("phase", "")
            fields["folder_id"] = folder_id
            fields.setdefault("key_context", "")
            fields.setdefault("now", now_str)

            guide = _art.render_resume_guide(**fields)
            for name in ("CLAUDE.md", "AGENTS.md"):
                binding.vfs.write(f"{folder}/{name}", guide)
                if f"{folder}/{name}" not in changed:
                    changed.append(f"{folder}/{name}")

            brief_path = f"{folder}/{BRIEF_NAME}"
            if binding.vfs.exists(brief_path):
                try:
                    r = parse_brief(binding.vfs.read_text(brief_path))
                    fm = r["frontmatter"]
                    status_val = str(fm.get("status") or "active")
                    if status_val in ("paused", "archived"):
                        status_val = "active"
                    updated_brief = render_brief(
                        id=fm.get("id", ""),
                        title=fm.get("title", ""),
                        status=status_val,
                        created=_as_iso_str(fm.get("created")) or now_date,
                        updated=now_date,
                        goal=r["goal"],
                        summary=r["summary"],
                        tags=fm.get("tags") or (),
                        kind=fm.get("kind") or "",
                        links=fm.get("links") or (),
                    )
                    binding.vfs.write(brief_path, updated_brief)
                    if brief_path not in changed:
                        changed.append(brief_path)
                except BriefError as exc:
                    raise ValueError(
                        f"invalid {BRIEF_NAME} for {folder_id}: {exc}"
                    ) from exc
            else:
                raise ValueError(f"work-folder 缺少 {BRIEF_NAME}: {folder_id}")

            index_md, _, _, _ = _render_index_snapshot(binding)
            binding.vfs.write("INDEX.md", index_md)
            if "INDEX.md" not in changed:
                changed.append("INDEX.md")

            return {
                "saved": True,
                "folder_id": folder_id,
                "written": [p.replace(f"{folder}/", "") for p in changed],
                "contract": SAVE_CONTRACT,
                "changed_paths": changed,
            }

        return self._call_mutate(
            "wf_save",
            args,
            _write,
            expected_base_sha,
            "work-folder: save",
            idempotency_key=idempotency_key,
            idempotency_payload={
                "folder_id": folder_id,
                "summary": summary,
                "context_snapshot": context_snapshot,
                "resume_fields": resume_fields,
                "golden_order_additions": golden_order_additions,
                "findings_addition": findings_addition,
            },
        )

    def resume(
        self,
        folder_id: str,
        now_fn,
        probe_fn=None,
        expected_base_sha: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        replay_args = {"folder_id": folder_id}
        replay = self._kernel.replay_idempotent(
            "work-folder",
            "wf_resume",
            replay_args,
            idempotency_key=idempotency_key,
            idempotency_payload=replay_args,
        )
        if replay is not None:
            return replay
        try:
            folder = self._folder_path(folder_id)
        except (BriefError, FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "folder_id": folder_id,
                "error": str(exc),
                "blocked": True,
            }

        has_progress = self._binding.vfs.exists(f"{folder}/progress.md")
        has_claude = self._binding.vfs.exists(f"{folder}/CLAUDE.md")
        if not has_progress and not has_claude:
            return {
                "ok": False,
                "error": "work-folder 缺少 progress.md 和 CLAUDE.md，无法恢复",
                "blocked": True,
            }

        now = now_fn()
        args = {
            "folder_id": folder_id,
            "now_hm": now.strftime("%H:%M:%S"),
            "now_date": now.strftime("%Y-%m-%d"),
            "now_str": now.strftime("%Y-%m-%d %H:%M"),
        }
        _probe_fn = probe_fn if probe_fn is not None else _ver.fs_git_probe

        def _write(binding, args):
            folder = self._folder_path(args["folder_id"])

            changed: list[str] = []
            now_hm = args["now_hm"]
            now_date = args["now_date"]

            _key_map = {
                "CLAUDE.md": "claude",
                "progress.md": "progress",
                "context.md": "context",
                "findings.md": "findings",
                "golden-order.md": "golden_order",
            }
            loaded: dict[str, str | None] = {}
            for fname, key in _key_map.items():
                p = f"{folder}/{fname}"
                if binding.vfs.exists(p):
                    loaded[key] = binding.vfs.read_text(p)
                else:
                    loaded[key] = None

            context_md = loaded.get("context") or ""
            resources = _ver.parse_context_paths(context_md)
            verdicts = _ver.verify_env(resources, probe_fn=_probe_fn)
            overall = _ver.overall_level(verdicts)
            blocked = (overall == _ver.BROKEN)

            n_match = sum(1 for v in verdicts if v.level == _ver.MATCH)
            n_drift = sum(1 for v in verdicts if v.level == _ver.DRIFT)
            n_broken = sum(1 for v in verdicts if v.level == _ver.BROKEN)

            if binding.vfs.exists(f"{folder}/progress.md"):
                progress = binding.vfs.read_text(f"{folder}/progress.md")
                row = _art.changelog_row(
                    now_hm, "resume",
                    f"环境验证: {n_match}✅ {n_drift}⚠️ {n_broken}❌",
                )
                updated_progress = _art.insert_changelog_row(progress, row)
                if updated_progress != progress:
                    binding.vfs.write(f"{folder}/progress.md", updated_progress)
                    changed.append(f"{folder}/progress.md")

            brief_path = f"{folder}/{BRIEF_NAME}"
            if binding.vfs.exists(brief_path):
                try:
                    r = parse_brief(binding.vfs.read_text(brief_path))
                    fm = r["frontmatter"]
                    status_val = str(fm.get("status") or "active")
                    if status_val in ("paused", "archived"):
                        status_val = "active"
                    updated_brief = render_brief(
                        id=fm.get("id", ""),
                        title=fm.get("title", ""),
                        status=status_val,
                        created=_as_iso_str(fm.get("created")) or now_date,
                        updated=now_date,
                        goal=r["goal"],
                        summary=r["summary"],
                        tags=fm.get("tags") or (),
                        kind=fm.get("kind") or "",
                        links=fm.get("links") or (),
                    )
                    binding.vfs.write(brief_path, updated_brief)
                    changed.append(brief_path)
                except BriefError as exc:
                    raise ValueError(
                        f"invalid {BRIEF_NAME} for {folder_id}: {exc}"
                    ) from exc
            else:
                raise ValueError(f"work-folder 缺少 {BRIEF_NAME}: {folder_id}")

            index_md, _, _, _ = _render_index_snapshot(binding)
            binding.vfs.write("INDEX.md", index_md)
            if "INDEX.md" not in changed:
                changed.append("INDEX.md")

            level_icon = {"MATCH": "✅", "DRIFT": "⚠️", "BROKEN": "❌"}
            verdict_lines = "\n".join(
                f"  {level_icon.get(v.level, '?')} {v.name} ({v.path}) — {v.detail}"
                for v in verdicts
            ) or "  （无关键路径资源）"

            resume_report = (
                f"[Resume 报告]\n"
                f"Work folder ID: {folder_id}\n"
                f"环境验证总体: {overall}\n\n"
                f"资源明细:\n{verdict_lines}\n\n"
                f"{'⚠️ 存在 BROKEN 资源，已阻塞，等待用户决策。' if blocked else '✅ 验证通过，可以继续工作。'}"
            )

            return {
                "ok": True,
                "folder_id": folder_id,
                "loaded": loaded,
                "verification": {
                    "overall": overall,
                    "verdicts": [asdict(v) for v in verdicts],
                },
                "blocked": blocked,
                "resume_report": resume_report,
                "contract": RESUME_BLOCKED_CONTRACT if blocked else RESUME_PROCEED_CONTRACT,
                "changed_paths": changed,
            }

        return self._call_mutate(
            "wf_resume",
            args,
            _write,
            expected_base_sha,
            "work-folder: resume",
            idempotency_key=idempotency_key,
            idempotency_payload=replay_args,
        )

    def append_progress(
        self,
        folder_id: str,
        entry: str,
        source_session_id: str,
        idempotency_key: str,
        *,
        now_fn,
        expected_base_sha: str | None = None,
    ) -> dict:
        """幂等追加 session progress，并原子 touch brief + rebuild INDEX。"""
        commit = head_sha(self._binding.repo_root) or ""
        if not isinstance(folder_id, str) or not re.fullmatch(
            r"wf-[0-9a-f]{6}", folder_id
        ):
            return _append_error(
                "INVALID_PATH",
                "folder_id must match wf-<6 lowercase hex>",
                folder_id=folder_id if isinstance(folder_id, str) else None,
                source_session_id=(
                    source_session_id
                    if isinstance(source_session_id, str)
                    else None
                ),
                commit=commit,
            )
        if not isinstance(entry, str) or not entry.strip():
            return _append_error(
                "INVALID_CONTENT",
                "entry must be a non-empty string",
                folder_id=folder_id,
                source_session_id=(
                    source_session_id
                    if isinstance(source_session_id, str)
                    else None
                ),
                commit=commit,
            )
        if len(entry.encode("utf-8")) > 1_000_000:
            return _append_error(
                "INVALID_CONTENT",
                "entry exceeds 1000000 bytes",
                folder_id=folder_id,
                source_session_id=(
                    source_session_id
                    if isinstance(source_session_id, str)
                    else None
                ),
                commit=commit,
            )
        if (
            not isinstance(source_session_id, str)
            or not source_session_id.strip()
            or len(source_session_id) > 512
        ):
            return _append_error(
                "INVALID_CONTENT",
                "source_session_id must be a non-empty string up to 512 chars",
                folder_id=folder_id,
                commit=commit,
            )
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 256
        ):
            return _append_error(
                "INVALID_CONTENT",
                "idempotency_key must be a non-empty string up to 256 chars",
                folder_id=folder_id,
                source_session_id=source_session_id,
                commit=commit,
            )

        request_fingerprint = _append_fingerprint(
            folder_id,
            entry,
            source_session_id,
        )
        idempotency_payload = {
            "folder_id": folder_id,
            "entry": entry,
            "source_session_id": source_session_id,
        }
        replay_args = {
            **idempotency_payload,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
        }
        with self._append_lock:
            try:
                replay = self._kernel.replay_idempotent(
                    "work-folder",
                    "wf_append_progress",
                    replay_args,
                    idempotency_key=idempotency_key,
                    idempotency_payload=idempotency_payload,
                )
            except IdempotencyConflictError:
                return _append_error(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency_key was already used for a different request",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    commit=head_sha(self._binding.repo_root) or "",
                )
            if replay is not None:
                replay["replayed"] = True
                replay["commit"] = replay.get("git", {}).get("detail", "")
                return replay

            try:
                folder = self._folder_path(folder_id)
            except FileNotFoundError:
                return _append_error(
                    "RESOURCE_NOT_FOUND",
                    "work folder not found",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    commit=commit,
                )
            except (BriefError, ValueError):
                return _append_error(
                    "INVALID_CONTENT",
                    "work folder identity is invalid",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    commit=commit,
                )
            progress_path = f"{folder}/progress.md"
            if not self._binding.vfs.exists(progress_path):
                return _append_error(
                    "INVALID_CONTENT",
                    "work folder is missing progress.md",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    commit=commit,
                )

            now = now_fn()
            args = {
                "folder_id": folder_id,
                "entry": entry,
                "source_session_id": source_session_id,
                "idempotency_key": idempotency_key,
                "request_fingerprint": request_fingerprint,
                "now_hm": now.strftime("%H:%M:%S"),
                "now_str": now.strftime("%Y-%m-%d %H:%M"),
                "now_date": now.strftime("%Y-%m-%d"),
            }
            write_ran = False

            def _write(binding, args):
                nonlocal write_ran
                write_ran = True

                folder = self._folder_path(args["folder_id"])
                progress_path = f"{folder}/progress.md"
                if not binding.vfs.exists(progress_path):
                    raise ValueError("work folder is missing progress.md")

                progress = binding.vfs.read_text(progress_path)
                key_hash = hashlib.sha256(
                    args["idempotency_key"].encode("utf-8")
                ).hexdigest()[:8]
                action = (
                    f"session:{_table_cell(args['source_session_id'])}:"
                    f"{key_hash}"
                )
                detail = _table_cell(args["entry"].strip())
                row = _art.changelog_row(args["now_hm"], action, detail)
                updated_progress = _art.insert_changelog_row(progress, row)
                binding.vfs.write(progress_path, updated_progress)

                brief_path = f"{folder}/{BRIEF_NAME}"
                parsed = parse_brief(binding.vfs.read_text(brief_path))
                fm = parsed["frontmatter"]
                status = str(fm.get("status") or "active")
                if status in ("paused", "archived"):
                    status = "active"
                updated_brief = render_brief(
                    id=args["folder_id"],
                    title=fm.get("title", ""),
                    status=status,
                    created=_as_iso_str(fm.get("created"))
                    or args["now_date"],
                    updated=args["now_date"],
                    goal=parsed["goal"],
                    summary=parsed["summary"],
                    tags=fm.get("tags") or (),
                    kind=fm.get("kind") or "",
                    links=fm.get("links") or (),
                )
                binding.vfs.write(brief_path, updated_brief)

                index_md, _, _, _ = _render_index_snapshot(binding)
                binding.vfs.write("INDEX.md", index_md)
                content_revision = "sha256:" + hashlib.sha256(
                    updated_progress.encode("utf-8")
                ).hexdigest()
                return {
                    "ok": True,
                    "appended": True,
                    "replayed": False,
                    "folder_id": args["folder_id"],
                    "id": args["folder_id"],
                    "filename": "progress.md",
                    "source_session_id": args["source_session_id"],
                    "idempotency_key": args["idempotency_key"],
                    "request_fingerprint": args["request_fingerprint"],
                    "content_revision": content_revision,
                    "updated": args["now_str"],
                    "changed_paths": [
                        progress_path,
                        brief_path,
                        "INDEX.md",
                    ],
                }

            try:
                result = self._call_mutate(
                    "wf_append_progress",
                    args,
                    _write,
                    expected_base_sha,
                    "work-folder: append progress",
                    idempotency_key=idempotency_key,
                    idempotency_payload=idempotency_payload,
                )
            except IdempotencyConflictError:
                return _append_error(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency_key was already used for a different request",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    commit=head_sha(self._binding.repo_root) or "",
                )
            except CASRejectionError:
                return _append_error(
                    "BASE_COMMIT_CONFLICT",
                    "repository changed since expected base commit",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    retryable=True,
                    commit=head_sha(self._binding.repo_root) or "",
                )
            except (BriefError, ValueError):
                return _append_error(
                    "INVALID_CONTENT",
                    "work folder changed to an invalid state during append",
                    folder_id=folder_id,
                    source_session_id=source_session_id,
                    commit=head_sha(self._binding.repo_root) or "",
                )

            result["replayed"] = not write_ran
            result["commit"] = result.get("git", {}).get("detail", "")
            return result

    def reindex(
        self,
        dry_run: bool = False,
        expected_base_sha: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        args = {"dry_run": dry_run}

        def _write(binding, args):
            dry_run = args["dry_run"]
            md, indexed, skipped, errors = _render_index_snapshot(binding)

            result: dict = {
                "indexed": indexed,
                "skipped": skipped,
                "errors": errors,
            }
            if dry_run:
                result["preview"] = md
            else:
                binding.vfs.write("INDEX.md", md)
                result["changed_paths"] = ["INDEX.md"]

            return result

        if dry_run:
            # dry_run is a pure read: do not enter the governed mutation path
            # with an empty changed-path allowlist.
            return _write(self._binding, args)

        return self._call_mutate(
            "wf_reindex",
            args,
            _write,
            expected_base_sha,
            "work-folder: reindex",
            idempotency_key=idempotency_key,
            idempotency_payload={"dry_run": False},
        )
