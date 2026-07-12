"""Wiki mutation store — governed via GovernedKernel.

WikiStore wraps a kernel binding and routes all wiki mutations through
GovernedKernel.mutate (CAS → policy → VFS → ledger → manifest → git commit),
following the composition pattern established by MemoryStore.
"""

from __future__ import annotations

from katana_kernel.kernel import GovernedKernel
from katana_kernel.policy import DomainPolicy, PolicyViolationError
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp.pages import parse_page, render_page


class WikiIngestRejectedError(PolicyViolationError):
    def __init__(self, rejected: dict[str, list[str]]):
        self.rejected = rejected
        super().__init__(f"ingest rejected: {list(rejected.keys())}")


def _wiki_policy() -> DomainPolicy:
    def _invariants(domain, op, args):
        if op == "ingest_apply":
            new_pages = args.get("new_pages") or []
            if not new_pages:
                raise WikiIngestRejectedError({"(proposal)": ["new_pages is empty"]})
            rejected: dict[str, list[str]] = {}
            for page in new_pages:
                fm = page.get("frontmatter") or {}
                body = page.get("body") or ""
                errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
                if errs:
                    rejected[page["path"]] = errs
            if rejected:
                raise WikiIngestRejectedError(rejected)

    return DomainPolicy(
        domain="wiki",
        allowed_ops={"ingest_apply", "search", "query", "ingest_plan", "list_docs", "lint_mechanical"},
        invariants=[_invariants],
    )


class WikiStore:
    def __init__(self, kernel: GovernedKernel):
        self._kernel = kernel
        self._binding = kernel.get_binding("wiki")

    def _scan_existing_ids(self) -> set[str]:
        ids: set[str] = set()
        for p in self._binding.vfs.ls("**/*.md"):
            try:
                text = self._binding.vfs.read_text(p)
                fm, _ = parse_page(text)
                pid = fm.get("id")
                if pid:
                    ids.add(pid)
            except Exception:
                pass
        return ids

    def ingest_apply(self, proposal: dict,
                     expected_base_sha: str | None = None) -> dict:
        store = self

        def _write(binding, args):
            new_pages = args.get("new_pages") or []
            log_line = args.get("log_line") or ""
            existing_ids = store._scan_existing_ids()

            written: list[str] = []
            backlinked: list[str] = []
            all_paths: list[str] = []

            for page in new_pages:
                page_id = binding.ledger.gen_id(existing_ids)
                existing_ids.add(page_id)
                fm = dict(page.get("frontmatter") or {})
                fm["id"] = page_id
                content = render_page(fm, page.get("body") or "")
                binding.vfs.write(page["path"], content, op="ingest_apply", args=args)
                written.append(page["path"])
                all_paths.append(page["path"])

            for page in new_pages:
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
                "written": written,
                "backlinked": backlinked,
                "changed_paths": all_paths,
            }

        try:
            result = self._call_mutate("ingest_apply", proposal, _write,
                                       expected_base_sha, "wiki: ingest")
        except WikiIngestRejectedError as e:
            return {"applied": False, "rejected": e.rejected}

        commit_sha = ""
        if result.get("git", {}).get("committed"):
            commit_sha = result["git"]["detail"]

        return {
            "applied": True,
            "written": result.get("written", []),
            "backlinked": result.get("backlinked", []),
            "commit": commit_sha,
            "git": result.get("git"),
            "manifest": result.get("manifest"),
        }

    def _call_mutate(self, op: str, args: dict, write_fn,
                     expected_base_sha: str | None, commit_msg: str) -> dict:
        return self._kernel.mutate(
            "wiki", op, args,
            expected_base_sha=expected_base_sha,
            write_fn=write_fn,
            commit_msg=commit_msg,
        )