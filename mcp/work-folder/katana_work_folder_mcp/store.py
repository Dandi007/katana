"""Work Folder mutation store — governed via GovernedKernel.

WorkFolderStore wraps a kernel binding and routes all work-folder mutations
through GovernedKernel.mutate (CAS → policy → VFS → ledger → manifest → git commit),
following the composition pattern established by MemoryStore and WikiStore.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict
from pathlib import Path

from katana_kernel.kernel import GovernedKernel
from katana_kernel.policy import DomainPolicy, PolicyViolationError
from katana_work_folder_mcp import artifacts as _art
from katana_work_folder_mcp import verify as _ver
from katana_work_folder_mcp.brief import BRIEF_NAME, BriefError, parse_brief, render_brief
from katana_work_folder_mcp.lifecycle import (
    RESUME_BLOCKED_CONTRACT,
    RESUME_PROCEED_CONTRACT,
    SAVE_CONTRACT,
    slugify,
)


def _wf_policy() -> DomainPolicy:
    def _invariants(domain, op, args):
        if op == "wf_create":
            if not args.get("topic"):
                raise PolicyViolationError("topic is required for wf_create")
        elif op in ("wf_save", "wf_resume"):
            if not args.get("folder"):
                raise PolicyViolationError("folder is required")

    return DomainPolicy(
        domain="work-folder",
        allowed_ops={"wf_create", "wf_save", "wf_resume", "wf_reindex"},
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


class WorkFolderStore:
    def __init__(self, kernel: GovernedKernel):
        self._kernel = kernel
        self._binding = kernel.get_binding("work-folder")

    def _call_mutate(self, op: str, args: dict, write_fn,
                     expected_base_sha: str | None, commit_msg: str) -> dict:
        return self._kernel.mutate(
            "work-folder", op, args,
            expected_base_sha=expected_base_sha,
            write_fn=write_fn,
            commit_msg=commit_msg,
        )

    def create(self, topic: str, now_fn,
               expected_base_sha: str | None = None) -> dict:
        now = now_fn()
        date_str = now.strftime("%Y/%m/%d")
        slug = slugify(topic)
        folder = f"{date_str}/{slug}"

        if self._binding.vfs.exists(folder):
            return {
                "created": False,
                "path": folder,
                "seeded": [],
                "note": "已存在",
            }

        args = {
            "topic": topic,
            "date_str": date_str,
            "now_str": now.strftime("%Y-%m-%d %H:%M"),
            "now_date": now.strftime("%Y-%m-%d"),
        }

        def _write(binding, args):
            topic = args["topic"]
            date_str = args["date_str"]
            now_str = args["now_str"]
            now_date = args["now_date"]

            slug = slugify(topic)
            folder = f"{date_str}/{slug}"

            existing_ids = _scan_brief_ids(binding.vfs)
            wf_id = binding.ledger.gen_id(existing_ids)

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

            return {
                "created": True,
                "path": folder,
                "id": wf_id,
                "seeded": ["progress.md", "context.md", BRIEF_NAME],
                "drafting": SAVE_CONTRACT,
                "changed_paths": changed,
            }

        return self._call_mutate("wf_create", args, _write,
                                 expected_base_sha, "work-folder: create")

    def save(self, folder: str, now_fn,
             summary: str = "checkpoint",
             context_snapshot: str | None = None,
             resume_fields: dict | None = None,
             golden_order_additions: str | None = None,
             findings_addition: str | None = None,
             expected_base_sha: str | None = None) -> dict:
        now = now_fn()
        args = {
            "folder": folder,
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
            folder = args["folder"]
            if not binding.vfs.exists(folder):
                raise FileNotFoundError(f"work-folder 不存在: {folder}")

            changed: list[str] = []
            now_str = args["now_str"]
            now_date = args["now_date"]
            now_hm = args["now_hm"]
            wf_abs = os.path.join(binding.repo_root, folder)

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
                    "wf_abs": wf_abs,
                    "key_context": "",
                    "now": now_str,
                }
            fields.setdefault("goal", "")
            fields.setdefault("status", "")
            fields.setdefault("phase", "")
            fields.setdefault("wf_abs", wf_abs)
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
                except BriefError:
                    pass
            else:
                existing_ids = _scan_brief_ids(binding.vfs)
                wf_id = binding.ledger.gen_id(existing_ids)
                goal = fields.get("goal", "")
                title = goal or args["summary"]
                brief = render_brief(
                    id=wf_id, title=title, status="active",
                    created=now_date, updated=now_date,
                    goal=goal, summary="",
                )
                binding.vfs.write(brief_path, brief)
                if brief_path not in changed:
                    changed.append(brief_path)

            return {
                "saved": True,
                "folder": folder,
                "written": [p.replace(f"{folder}/", "") for p in changed],
                "contract": SAVE_CONTRACT,
                "changed_paths": changed,
            }

        return self._call_mutate("wf_save", args, _write,
                                 expected_base_sha, "work-folder: save")

    def resume(self, folder: str, now_fn,
               probe_fn=None,
               expected_base_sha: str | None = None) -> dict:
        if not self._binding.vfs.exists(folder):
            return {
                "ok": False,
                "error": f"work-folder 不存在: {folder}",
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
            "folder": folder,
            "now_hm": now.strftime("%H:%M:%S"),
            "now_date": now.strftime("%Y-%m-%d"),
            "now_str": now.strftime("%Y-%m-%d %H:%M"),
        }
        _probe_fn = probe_fn if probe_fn is not None else _ver.fs_git_probe

        def _write(binding, args):
            folder = args["folder"]

            changed: list[str] = []
            now_hm = args["now_hm"]
            now_date = args["now_date"]
            wf_abs = os.path.join(binding.repo_root, folder)

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
                except BriefError:
                    pass
            else:
                try:
                    existing_ids = _scan_brief_ids(binding.vfs)
                    wf_id = binding.ledger.gen_id(existing_ids)
                    pg_goal = ""
                    m = re.search(r"\*\*Goal:\*\*\s*(.+)", loaded.get("progress") or "")
                    if m:
                        pg_goal = m.group(1).strip()
                    title = pg_goal or folder
                    brief = render_brief(
                        id=wf_id, title=title, status="active",
                        created=now_date, updated=now_date,
                        goal=pg_goal, summary="",
                    )
                    binding.vfs.write(brief_path, brief)
                    if brief_path not in changed:
                        changed.append(brief_path)
                except Exception:
                    pass

            level_icon = {"MATCH": "✅", "DRIFT": "⚠️", "BROKEN": "❌"}
            verdict_lines = "\n".join(
                f"  {level_icon.get(v.level, '?')} {v.name} ({v.path}) — {v.detail}"
                for v in verdicts
            ) or "  （无关键路径资源）"

            resume_report = (
                f"[Resume 报告]\n"
                f"Work folder: {wf_abs}\n"
                f"环境验证总体: {overall}\n\n"
                f"资源明细:\n{verdict_lines}\n\n"
                f"{'⚠️ 存在 BROKEN 资源，已阻塞，等待用户决策。' if blocked else '✅ 验证通过，可以继续工作。'}"
            )

            return {
                "ok": True,
                "folder": folder,
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

        return self._call_mutate("wf_resume", args, _write,
                                 expected_base_sha, "work-folder: resume")

    def reindex(self, dry_run: bool = False,
                expected_base_sha: str | None = None) -> dict:
        args = {"dry_run": dry_run}

        def _write(binding, args):
            dry_run = args["dry_run"]
            entries: list[dict] = []
            errors: list[str] = []

            for brief_path in binding.vfs.ls("**/_brief.md"):
                try:
                    r = parse_brief(binding.vfs.read_text(brief_path))
                    folder = str(Path(brief_path).parent)
                    entries.append({
                        "folder": folder,
                        "fm": r["frontmatter"],
                        "goal": r["goal"],
                    })
                except BriefError as e:
                    errors.append(f"{brief_path}: {e}")
                except Exception as e:
                    errors.append(f"{brief_path}: {e}")

            brief_folders = {Path(e["folder"]) for e in entries}
            skipped = 0
            for progress_path in binding.vfs.ls("**/progress.md"):
                pfolder = str(Path(progress_path).parent)
                if pfolder not in brief_folders:
                    skipped += 1

            def _updated_key(e):
                v = e["fm"].get("updated", "")
                if hasattr(v, "isoformat"):
                    return v.isoformat()
                return str(v) if v else ""

            sorted_entries = sorted(entries, key=_updated_key, reverse=True)
            lines = [
                "# Work Folder INDEX", "",
                f"> 共 {len(sorted_entries)} 个 work folder，按 updated 倒序。由 wf-reindex 自动生成，勿手改。",
                "",
                "| updated | status | id | title | goal | folder |",
                "|---|---|---|---|---|---|",
            ]
            for e in sorted_entries:
                fm = e["fm"]
                updated = _updated_key(e)
                status = fm.get("status", "")
                id_ = fm.get("id", "")
                title = fm.get("title", "")
                goal = (e["goal"] or "").replace("|", "\\|")
                folder = e["folder"]
                lines.append(
                    f"| {updated} | {status} | {id_} | {title} | {goal} | `{folder}` |"
                )
            lines.append("")
            md = "\n".join(lines)

            index_path_abs = os.path.join(binding.repo_root, "INDEX.md")

            result: dict = {
                "indexed": len(entries),
                "skipped": skipped,
                "errors": errors,
                "index_path": index_path_abs,
            }
            if dry_run:
                result["preview"] = md
            else:
                binding.vfs.write("INDEX.md", md)
                result["changed_paths"] = ["INDEX.md"]

            return result

        return self._call_mutate("wf_reindex", args, _write,
                                 expected_base_sha, "work-folder: reindex")