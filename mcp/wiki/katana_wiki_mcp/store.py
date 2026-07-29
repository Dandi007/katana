"""Wiki mutation store — governed via GovernedKernel.

WikiStore wraps a kernel binding and routes all wiki mutations through
GovernedKernel.mutate (CAS → policy → VFS → ledger → manifest → git commit),
following the composition pattern established by MemoryStore.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from katana_kernel.kernel import GovernedKernel
from katana_kernel.policy import DomainPolicy, PolicyViolationError
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp.ingest import is_governed_writable_path, normalize_proposal
from katana_wiki_mcp.pages import parse_page, render_page


class WikiIngestRejectedError(PolicyViolationError):
    def __init__(self, rejected: dict[str, list[str]]):
        self.rejected = rejected
        super().__init__(f"ingest rejected: {list(rejected.keys())}")


def _wiki_policy() -> DomainPolicy:
    def _invariants(domain, op, args):
        if op == "ingest_apply":
            new_pages = args.get("new_pages") or []
            updates = args.get("updates") or []
            if not isinstance(args.get("log_line", ""), str):
                raise WikiIngestRejectedError(
                    {"(proposal)": ["log_line must be a string"]}
                )
            if not new_pages and not updates:
                raise WikiIngestRejectedError(
                    {"(proposal)": ["new_pages and updates are both empty"]}
                )
            rejected: dict[str, list[str]] = {}
            for kind, pages in (("new_pages", new_pages), ("updates", updates)):
                if not isinstance(pages, list):
                    rejected.setdefault("(proposal)", []).append(f"{kind} must be a list")
                    continue
                for page in pages:
                    if not isinstance(page, dict):
                        rejected.setdefault("(proposal)", []).append(
                            f"{kind} entries must be objects"
                        )
                        continue
                    path = page.get("path")
                    if not isinstance(path, str) or not path:
                        rejected.setdefault("(proposal)", []).append(
                            f"{kind} entry missing path"
                        )
                        continue
                    fm = page.get("frontmatter")
                    body = page.get("body")
                    back_updates = page.get("back_updates", [])
                    structural_errors: list[str] = []
                    if not isinstance(fm, dict):
                        structural_errors.append("frontmatter must be an object")
                    if not isinstance(body, str):
                        structural_errors.append("body must be a string")
                    if not isinstance(back_updates, list) or any(
                        not isinstance(bu, dict)
                        or not isinstance(bu.get("path"), str)
                        or not bu.get("path")
                        or not isinstance(bu.get("title"), str)
                        or not bu.get("title")
                        for bu in back_updates
                    ):
                        structural_errors.append(
                            "back_updates must contain non-empty path/title objects"
                        )
                    if structural_errors:
                        rejected[path] = structural_errors
                        continue
                    errs = _inv.validate_page(
                        fm, body, require_summary=True, require_sources=True
                    )
                    if kind == "new_pages" and fm.get("id"):
                        errs.append("new_pages 不得指定 id；existing page 必须走 updates")
                    if errs:
                        rejected[path] = errs
            if rejected:
                raise WikiIngestRejectedError(rejected)

        if op.startswith("fs_") and op not in ("fs_batch", "fs_capabilities", "fs_resolve",
                                                  "fs_stat", "fs_list", "fs_glob", "fs_read"):
            content = args.get("content")
            if content is not None:
                fm, body = parse_page(content)
                errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
                if errs:
                    raise ValueError("; ".join(errs))

    return DomainPolicy(
        domain="wiki",
        allowed_ops={"ingest_apply", "gap_log", "delete",
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename",
            "fs_delete", "fs_batch"},
        invariants=[_invariants],
    )


class WikiStore:
    def __init__(self, kernel: GovernedKernel):
        self._kernel = kernel
        self._binding = kernel.get_binding("wiki")

    def _scan_existing_state(
        self,
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        id_paths: dict[str, list[str]] = {}
        nfc_paths: dict[str, list[str]] = {}
        for p in self._binding.vfs.ls("**/*.md"):
            normalized_path = unicodedata.normalize("NFC", p)
            if is_governed_writable_path(normalized_path):
                nfc_paths.setdefault(normalized_path, []).append(p)
            try:
                text = self._binding.vfs.read_text(p)
                fm, _ = parse_page(text)
                pid = fm.get("id") if isinstance(fm, dict) else None
                if pid and is_governed_writable_path(normalized_path):
                    id_paths.setdefault(str(pid), []).append(p)
            except Exception:
                pass
        return id_paths, nfc_paths

    def ingest_apply(self, proposal: dict,
                     expected_base_sha: str | None = None) -> dict:
        store = self
        normalized, rejected = normalize_proposal(
            proposal, self._binding.repo_root
        )
        updates = normalized.get("updates", [])
        if updates and not expected_base_sha:
            rejected.setdefault("(proposal)", []).append(
                "updates require expected_base_sha from wiki_ingest_plan"
            )
        if expected_base_sha is not None and (
            not isinstance(expected_base_sha, str) or len(expected_base_sha) != 40
        ):
            rejected.setdefault("(proposal)", []).append(
                "expected_base_sha must be a 40-character commit SHA"
            )
        if rejected:
            return {"applied": False, "rejected": rejected}

        def _write(binding, args):
            new_pages = args.get("new_pages") or []
            updates = args.get("updates") or []
            log_line = args.get("log_line") or ""
            id_paths, nfc_paths = store._scan_existing_state()
            existing_ids = set(id_paths)

            # Resolve create/update intent after CAS and before the first write.
            # The proposal is rejected as a unit on any ambiguity.
            rejected: dict[str, list[str]] = {}
            duplicates = {
                pid: paths for pid, paths in id_paths.items() if len(paths) > 1
            }
            if duplicates:
                rejected.setdefault("(proposal)", []).append(
                    f"existing duplicate ids: {duplicates}"
                )
            path_collisions = {
                path: actuals
                for path, actuals in nfc_paths.items()
                if len(actuals) > 1
            }
            if path_collisions:
                rejected.setdefault("(proposal)", []).append(
                    f"existing NFC path collisions: {path_collisions}"
                )
            proposed_paths: set[str] = set()
            for page in [*new_pages, *updates]:
                path = page["path"]
                if path in proposed_paths:
                    rejected.setdefault(path, []).append(
                        "同一路径在 proposal 中重复或同时出现在 create/update"
                    )
                proposed_paths.add(path)

            for page in new_pages:
                path = page["path"]
                exact_path = Path(binding.repo_root) / path
                try:
                    exact_path.lstat()
                    exact_exists = True
                except FileNotFoundError:
                    exact_exists = False
                except OSError as exc:
                    rejected.setdefault(path, []).append(
                        f"exact path lstat failed: {exc.__class__.__name__}"
                    )
                    exact_exists = False
                if exact_exists or path in nfc_paths:
                    rejected.setdefault(path, []).append(
                        "new_pages path 已存在；existing page 必须走 updates"
                    )

            for page in updates:
                path = page["path"]
                exact_path = Path(binding.repo_root) / path
                try:
                    exact_path.lstat()
                    exact_exists = True
                except FileNotFoundError:
                    exact_exists = False
                except OSError as exc:
                    rejected.setdefault(path, []).append(
                        f"exact path lstat failed: {exc.__class__.__name__}"
                    )
                    exact_exists = False
                actual_paths = nfc_paths.get(path, [])
                if not exact_exists or not actual_paths:
                    rejected.setdefault(path, []).append(
                        "update path 不存在；新页面必须走 new_pages"
                    )
                    continue
                if len(actual_paths) != 1 or actual_paths[0] != path:
                    rejected.setdefault(path, []).append(
                        "update target path is not uniquely NFC canonical"
                    )
                    continue
                try:
                    current_fm, _ = parse_page(binding.vfs.read_text(path))
                except Exception as exc:
                    rejected.setdefault(path, []).append(
                        f"existing page frontmatter 解析失败: {exc.__class__.__name__}"
                    )
                    continue
                if not isinstance(current_fm, dict):
                    rejected.setdefault(path, []).append(
                        "existing page frontmatter must be a mapping"
                    )
                    continue
                current_id = current_fm.get("id")
                proposed_fm = page.get("frontmatter") or {}
                proposed_id = proposed_fm.get("id")
                if not current_id:
                    if proposed_id:
                        rejected.setdefault(path, []).append(
                            "legacy page has no id; update must not add or forge id"
                        )
                elif proposed_id != current_id:
                    rejected.setdefault(path, []).append(
                        f"update id/path mismatch：path 当前 id={current_id!r}"
                    )
                elif len(id_paths.get(str(current_id), [])) != 1:
                    rejected.setdefault(path, []).append(
                        "update target id is not unique"
                    )
                old_sources = current_fm.get("sources", [])
                new_sources = proposed_fm.get("sources", [])
                if (
                    not isinstance(old_sources, list)
                    or not isinstance(new_sources, list)
                    or not all(isinstance(item, str) for item in old_sources)
                    or not all(isinstance(item, str) for item in new_sources)
                    or not set(old_sources).issubset(set(new_sources))
                ):
                    rejected.setdefault(path, []).append(
                        "update sources must be a superset of existing sources"
                    )

            for page in [*new_pages, *updates]:
                for back_update in page.get("back_updates") or []:
                    back_path = back_update["path"]
                    if not binding.vfs.exists(back_path):
                        rejected.setdefault(page["path"], []).append(
                            f"back_update path 不存在: {back_path}"
                        )

            if rejected:
                raise WikiIngestRejectedError(rejected)

            written: list[str] = []
            created: list[str] = []
            updated: list[str] = []
            backlinked: list[str] = []
            all_paths: list[str] = []

            first_page_id = (
                (updates[0].get("frontmatter") or {}).get("id") if updates else None
            )
            for page in new_pages:
                page_id = binding.ledger.gen_id(existing_ids)
                existing_ids.add(page_id)
                if first_page_id is None:
                    first_page_id = page_id
                fm = dict(page.get("frontmatter") or {})
                fm["id"] = page_id
                content = render_page(fm, page.get("body") or "")
                binding.vfs.write(page["path"], content, op="ingest_apply", args=args)
                written.append(page["path"])
                created.append(page["path"])
                all_paths.append(page["path"])

            for page in updates:
                content = render_page(
                    dict(page.get("frontmatter") or {}), page.get("body") or ""
                )
                binding.vfs.write(page["path"], content, op="ingest_apply", args=args)
                written.append(page["path"])
                updated.append(page["path"])
                all_paths.append(page["path"])

            for page in [*new_pages, *updates]:
                for bu in page.get("back_updates") or []:
                    bu_path = bu["path"]
                    if binding.vfs.exists(bu_path):
                        current = binding.vfs.read_text(bu_path)
                        link = f"[[{bu['title']}]]"
                        if link not in current:
                            new_content = current.rstrip("\n") + f"\n- 关联：{link}\n"
                            binding.vfs.write(bu_path, new_content, op="ingest_apply", args=args)
                            backlinked.append(bu_path)
                            if bu_path not in all_paths:
                                all_paths.append(bu_path)

            if log_line:
                if binding.vfs.exists("log.md"):
                    log_content = binding.vfs.read_text("log.md")
                else:
                    log_content = ""
                binding.vfs.write("log.md", log_content + log_line + "\n", op="ingest_apply", args=args)
                if "log.md" not in all_paths:
                    all_paths.append("log.md")

            return {
                "id": first_page_id or "unknown",
                "written": written,
                "created": created,
                "updated": updated,
                "backlinked": backlinked,
                "changed_paths": all_paths,
            }

        try:
            result = self._call_mutate("ingest_apply", normalized, _write,
                                       expected_base_sha, "wiki: ingest")
        except WikiIngestRejectedError as e:
            return {"applied": False, "rejected": e.rejected}

        commit_sha = ""
        if result.get("git", {}).get("committed"):
            commit_sha = result["git"]["detail"]

        return {
            "applied": True,
            "written": result.get("written", []),
            "created": result.get("created", []),
            "updated": result.get("updated", []),
            "backlinked": result.get("backlinked", []),
            "commit": commit_sha,
            "git": result.get("git"),
            "manifest": result.get("manifest"),
        }

    def append_gap_log(self, line: str) -> dict:
        def _write(binding, args):
            line = args["line"]
            if binding.vfs.exists("log.md"):
                log_content = binding.vfs.read_text("log.md")
            else:
                log_content = ""
            binding.vfs.write("log.md", log_content + line + "\n", op="gap_log", args=args)
            return {"changed_paths": ["log.md"]}
        return self._call_mutate("gap_log", {"line": line}, _write, None, "wiki: query gap-log")

    def _call_mutate(self, op: str, args: dict, write_fn,
                     expected_base_sha: str | None, commit_msg: str) -> dict:
        return self._kernel.mutate(
            "wiki", op, args,
            expected_base_sha=expected_base_sha,
            write_fn=write_fn,
            commit_msg=commit_msg,
        )
