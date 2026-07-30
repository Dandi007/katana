"""v2 wiki store — git-backed mutation with manifests, single-write lock, index sync.

Every mutation: validate → write files → update index → git commit + manifest.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import threading
from pathlib import Path

from katana_wiki_v2_mcp import invariants as _inv
from katana_wiki_v2_mcp import pages as _pages
from katana_wiki_v2_mcp import search as _search


class StoreError(Exception):
    def __init__(self, response: dict):
        super().__init__(response.get("message", "store error"))
        self.response = response


class WikiStore:
    _manifest_counter: int = 0

    def __init__(self, data_root: str, embedding_client=None):
        self._data_root = data_root
        self._lock = threading.Lock()
        self._search = _search.WikiSearch(data_root, embedding_client)
        self._ensure_repo()
        self._ensure_dirs()
        self._load_index()

    def _ensure_repo(self) -> None:
        root = Path(self._data_root)
        git_dir = root / ".git"
        if not git_dir.is_dir():
            raise RuntimeError(f"data_root is not a git repository: {self._data_root}")

    def _ensure_dirs(self) -> None:
        root = Path(self._data_root)
        (root / "pages").mkdir(parents=True, exist_ok=True)
        (root / ".katana" / "manifests").mkdir(parents=True, exist_ok=True)
        (root / ".katana" / "index").mkdir(parents=True, exist_ok=True)

        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".katana/index/\n")

    def _load_index(self) -> None:
        self._search._ensure_lancedb()
        if self._search._table is not None:
            try:
                self._search._db.drop_table("pages")
                import pyarrow as pa
                dim = self._search._vector_dim()
                self._search._table = self._search._db.create_table("pages", pa.table({
                    "id": pa.array([], type=pa.string()),
                    "vector": pa.array([], type=pa.list_(pa.float32(), dim)),
                }))
            except Exception as e:
                self._search._last_error = str(e)
                self._search._mode = "keyword_only"
        self._search._keyword = _search.KeywordIndex()
        self._search._degraded_pages = set()
        pages = _pages.scan_pages(self._data_root)
        for page in pages:
            if page["id"] and not page.get("_error"):
                self._search.index_page(page["id"], page["title"], page["body"])

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", self._data_root, *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _git_quiet(self, *args: str) -> bool:
        result = subprocess.run(
            ["git", "-C", self._data_root, *args],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _head_sha(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _commit(self, message: str, paths: list[str]) -> str:
        self._git("add", "--", *paths)
        diff_result = subprocess.run(
            ["git", "-C", self._data_root, "diff", "--cached", "--quiet"],
            capture_output=True,
            text=True,
        )
        if diff_result.returncode == 0:
            return self._head_sha()
        self._git("commit", "-m", message)
        return self._head_sha()

    def _write_manifest(self, tool: str, changed_paths: list[str], result: dict) -> str:
        now = datetime.datetime.now()
        ts = now.strftime("%Y%m%dT%H%M%S")
        WikiStore._manifest_counter += 1
        ts = f"{ts}.{now.microsecond:06d}.{WikiStore._manifest_counter:04d}"
        manifest = {
            "tool": tool,
            "timestamp": now.isoformat(),
            "changed_paths": changed_paths,
            "result": result,
        }
        manifest_path = Path(self._data_root) / ".katana" / "manifests" / f"{ts}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        return str(manifest_path)

    def _mutate(self, tool: str, write_fn, commit_msg: str) -> dict:
        with self._lock:
            saved_keyword = {k: set(v) for k, v in self._search._keyword._index.items()}
            saved_lance_ids: set[str] = set()
            if self._search._table is not None:
                try:
                    saved_lance_ids = {r["id"] for r in self._search._table.to_list()}
                except Exception:
                    pass

            changed_paths: list[str] = []
            try:
                write_result = write_fn(changed_paths)
            except Exception:
                self._git_quiet("checkout", "--", ".")
                self._git_quiet("clean", "-fd", "pages/")
                self._search._keyword._index = saved_keyword
                if self._search._table is not None:
                    try:
                        current_ids = {r["id"] for r in self._search._table.to_list()}
                        for pid in current_ids - saved_lance_ids:
                            self._search._table.delete(f"id = '{pid}'")
                        for pid in saved_lance_ids - current_ids:
                            page = _pages.find_page_by_id(self._search._data_root, pid)
                            if page and page.get("id") and not page.get("_error"):
                                self._search._embed_page(pid, f"{page['title']}\n{page['body']}")
                    except Exception:
                        pass
                raise
            manifest_path = self._write_manifest(tool, changed_paths, write_result)
            all_paths = list(changed_paths) + [manifest_path]
            commit_sha = self._commit(commit_msg, all_paths)
            return {
                "commit": commit_sha,
                "manifest": manifest_path,
                **write_result,
            }

    def _status_clean(self) -> bool:
        result = subprocess.run(
            ["git", "-C", self._data_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == ""

    def _check_page_accessible(self, page: dict | None, ref: str) -> dict | None:
        if page is None:
            return {"code": "NOT_FOUND", "message": f"page not found: {ref}"}
        if page.get("_error"):
            return {"code": "VALIDATION_FAILED", "message": f"page is unparseable: {ref} ({page['_error']})"}
        if not page.get("id"):
            return {"code": "VALIDATION_FAILED", "message": f"page has no valid id: {ref}"}
        return None

    # ── read tools ──────────────────────────────────────────────────────────

    def wiki_get(self, ref: str) -> dict:
        page = _pages.find_page_by_ref(self._data_root, ref)
        err = self._check_page_accessible(page, ref)
        if err is not None:
            return err
        inlinks = _pages.compute_inlinks(self._data_root, page["title"])
        outlinks = _pages.compute_outlinks(page["body"])
        return {
            "id": page["id"],
            "title": page["title"],
            "frontmatter": page["frontmatter"],
            "body": page["body"],
            "inlinks": inlinks,
            "outlinks": outlinks,
        }

    def wiki_read(self, ref: str, offset: int | None = None, limit: int | None = None) -> dict:
        page = _pages.find_page_by_ref(self._data_root, ref)
        err = self._check_page_accessible(page, ref)
        if err is not None:
            return err
        body = page["body"]
        lines = body.split("\n")
        total = len(lines)
        start = max(1, offset or 1)
        last = min(total, start + limit - 1) if limit is not None else total
        if start > total or start > last:
            rendered = ""
        else:
            rendered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, last + 1))
        return {
            "id": page["id"],
            "title": page["title"],
            "rendered": rendered,
            "total_lines": total,
            "offset": start,
            "limit": limit,
        }

    def wiki_list(self, prefix: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
        all_pages = _pages.scan_pages(self._data_root)
        items = []
        for p in all_pages:
            if prefix and not p["title"].startswith(prefix):
                continue
            items.append({
                "id": p["id"],
                "title": p["title"],
                "摘要": p["frontmatter"].get("摘要", ""),
            })
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item["id"] == cursor:
                    start_idx = i + 1
                    break
        page_items = items[start_idx:start_idx + limit]
        next_cursor = page_items[-1]["id"] if len(page_items) == limit and start_idx + limit < len(items) else None
        return {"items": page_items, "cursor": next_cursor, "total": len(items)}

    def wiki_search(self, query: str, top_k: int = 10) -> dict:
        resp = self._search.search(query, top_k=top_k)
        results = []
        for r in resp["results"]:
            page = _pages.find_page_by_id(self._data_root, r["id"])
            if page is None:
                continue
            snippet = page["body"][:200] if page["body"] else ""
            results.append({
                "id": r["id"],
                "title": page["title"],
                "score": r["score"],
                "snippet": snippet,
            })
        return {
            "results": results,
            "index_health": resp["index_health"],
        }

    def wiki_query(self, question: str, top_k: int = 10) -> dict:
        from katana_wiki_v2_mcp import query as _query
        def _search_fn(q, top_k=top_k):
            return self.wiki_search(q, top_k=top_k)

        def _log_fn(line):
            self._append_gap_log_raw(line)

        return _query._do_query(
            question, top_k,
            search_fn=_search_fn,
            log_fn=_log_fn,
            now_fn=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    def wiki_report_gap(self, question: str, note: str | None = None) -> dict:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"## [{ts}] query | gap: {question}"
        if note:
            line += f" — {note}"
        return self._append_gap_log_raw(line)

    def _append_gap_log_raw(self, line: str) -> dict:
        def _write(changed_paths):
            log_path = Path(self._data_root) / "log.md"
            if log_path.exists():
                content = log_path.read_text(encoding="utf-8")
            else:
                content = ""
            log_path.write_text(content + line + "\n", encoding="utf-8")
            changed_paths.append("log.md")
            return {"written": True}

        return self._mutate("wiki_report_gap", _write, "wiki: gap log")

    # ── write tools ─────────────────────────────────────────────────────────

    def _validate_create_page(self, title: str, body: str, frontmatter: dict,
                              allow_no_outlink: bool = False,
                              extra_titles: set[str] | None = None) -> dict | None:
        title_err = _pages.validate_title(title)
        if title_err:
            return {"code": "VALIDATION_FAILED", "message": title_err}

        if extra_titles and title in extra_titles:
            return {
                "code": "TITLE_EXISTS",
                "message": f"title already exists in batch: {title}",
            }

        if frontmatter.get("id"):
            return {"code": "VALIDATION_FAILED", "message": "create must not specify id; id is server-assigned"}

        full_fm = dict(frontmatter)
        full_fm["id"] = None
        errs = _inv.validate_page(full_fm, body, require_summary=True, require_sources=True)
        if allow_no_outlink:
            errs = [e for e in errs if "无 outlink" not in e]

        if errs:
            return {"code": "VALIDATION_FAILED", "message": "; ".join(errs)}

        return None

    def _check_title_exists_locked(self, title: str) -> dict | None:
        existing = _pages.find_page_by_title(self._data_root, title)
        if existing is not None:
            if existing.get("_error"):
                return {
                    "code": "TITLE_EXISTS",
                    "message": f"title already exists but page is unparseable: {title} ({existing['_error']})",
                    "existing_id": None,
                }
            return {
                "code": "TITLE_EXISTS",
                "message": f"title already exists: {title}",
                "existing_id": existing["id"],
                "existing_摘要": existing["frontmatter"].get("摘要", ""),
            }
        return None

    def wiki_create(self, title: str, body: str, frontmatter: dict,
                    allow_no_outlink: bool = False) -> dict:
        err = self._validate_create_page(title, body, frontmatter, allow_no_outlink=allow_no_outlink)
        if err is not None:
            return err

        full_fm = dict(frontmatter)
        full_fm["id"] = None
        full_body = body
        store = self

        def _write(changed_paths):
            conflict = store._check_title_exists_locked(title)
            if conflict is not None:
                raise StoreError(conflict)
            new_id = _pages.make_id(existing_ids={p["id"] for p in _pages.scan_pages(store._data_root) if p["id"]})
            full_fm["id"] = new_id
            page_path = _pages.title_to_path(title)
            full = Path(store._data_root) / page_path
            _pages.write_page(str(full), full_fm, full_body)
            changed_paths.append(page_path)
            store._search.index_page(new_id, title, full_body)
            return {"id": new_id, "path": page_path}

        try:
            return self._mutate("wiki_create", _write, f"wiki: create {title}")
        except StoreError as e:
            return e.response

    def wiki_update(self, ref: str, body: str, frontmatter: dict | None = None) -> dict:
        page = _pages.find_page_by_ref(self._data_root, ref)
        err = self._check_page_accessible(page, ref)
        if err is not None:
            return err

        if frontmatter and frontmatter.get("id") and frontmatter["id"] != page["id"]:
            return {"code": "REF_MISMATCH", "message": f"id mismatch: expected {page['id']}, got {frontmatter['id']}"}

        store = self

        def _write(changed_paths):
            current_page = _pages.find_page_by_ref(store._data_root, ref)
            access_err = store._check_page_accessible(current_page, ref)
            if access_err is not None:
                raise StoreError(access_err)
            new_fm = dict(frontmatter) if frontmatter else dict(current_page["frontmatter"])
            new_fm["id"] = current_page["id"]
            errs = _inv.validate_edit_grade(current_page["frontmatter"], current_page["body"], new_fm, body)
            if errs:
                raise StoreError({"code": "VALIDATION_FAILED", "message": "; ".join(errs)})
            page_path = _pages.title_to_path(current_page["title"])
            full = Path(store._data_root) / page_path
            _pages.write_page(str(full), new_fm, body)
            changed_paths.append(page_path)
            store._search.remove_page(current_page["id"])
            store._search.index_page(current_page["id"], current_page["title"], body)
            return {"id": current_page["id"], "path": page_path}

        try:
            return self._mutate("wiki_update", _write, f"wiki: update {page['title']}")
        except StoreError as e:
            return e.response

    def wiki_edit(self, ref: str, old_string: str, new_string: str) -> dict:
        page = _pages.find_page_by_ref(self._data_root, ref)
        err = self._check_page_accessible(page, ref)
        if err is not None:
            return err

        store = self

        def _write(changed_paths):
            current_page = _pages.find_page_by_ref(store._data_root, ref)
            access_err = store._check_page_accessible(current_page, ref)
            if access_err is not None:
                raise StoreError(access_err)
            body = current_page["body"]
            count = body.count(old_string)
            if count == 0:
                raise StoreError({"code": "VALIDATION_FAILED", "message": "old_string not found in page body"})
            if count > 1:
                raise StoreError({"code": "VALIDATION_FAILED", "message": f"old_string matches {count} times; must be unique"})
            new_body = body.replace(old_string, new_string, 1)
            new_fm = dict(current_page["frontmatter"])
            errs = _inv.validate_edit_grade(current_page["frontmatter"], current_page["body"], new_fm, new_body)
            if errs:
                raise StoreError({"code": "VALIDATION_FAILED", "message": "; ".join(errs)})
            page_path = _pages.title_to_path(current_page["title"])
            full = Path(store._data_root) / page_path
            _pages.write_page(str(full), new_fm, new_body)
            changed_paths.append(page_path)
            store._search.remove_page(current_page["id"])
            store._search.index_page(current_page["id"], current_page["title"], new_body)
            return {"id": current_page["id"], "path": page_path}

        try:
            return self._mutate("wiki_edit", _write, f"wiki: edit {page['title']}")
        except StoreError as e:
            return e.response

    def wiki_rename(self, ref: str, new_title: str) -> dict:
        page = _pages.find_page_by_ref(self._data_root, ref)
        err = self._check_page_accessible(page, ref)
        if err is not None:
            return err

        title_err = _pages.validate_title(new_title)
        if title_err:
            return {"code": "VALIDATION_FAILED", "message": title_err}

        store = self

        def _write(changed_paths):
            current_page = _pages.find_page_by_ref(store._data_root, ref)
            access_err = store._check_page_accessible(current_page, ref)
            if access_err is not None:
                raise StoreError(access_err)

            old_title = current_page["title"]

            conflict = store._check_title_exists_locked(new_title)
            if conflict is not None:
                raise StoreError(conflict)

            old_path = _pages.title_to_path(old_title)
            new_path = _pages.title_to_path(new_title)

            old_full = Path(store._data_root) / old_path
            new_full = Path(store._data_root) / new_path
            old_full.rename(new_full)
            changed_paths.append(old_path)
            changed_paths.append(new_path)

            new_page_body = _pages.rewrite_wikilinks(current_page["body"], old_title, new_title)
            if new_page_body != current_page["body"]:
                _pages.write_page(str(new_full), current_page["frontmatter"], new_page_body)

            all_pages = _pages.scan_pages(store._data_root)
            for other_page in all_pages:
                if other_page.get("_error"):
                    continue
                if other_page["id"] == current_page["id"]:
                    continue
                new_body = _pages.rewrite_wikilinks(other_page["body"], old_title, new_title)
                if new_body != other_page["body"]:
                    other_path = _pages.title_to_path(other_page["title"])
                    full = Path(store._data_root) / other_path
                    _pages.write_page(str(full), other_page["frontmatter"], new_body)
                    changed_paths.append(other_path)
                    store._search.remove_page(other_page["id"])
                    store._search.index_page(other_page["id"], other_page["title"], new_body)

            store._search.remove_page(current_page["id"])
            store._search.index_page(current_page["id"], new_title, new_page_body)

            return {"id": current_page["id"], "old_title": old_title, "new_title": new_title}

        try:
            return self._mutate("wiki_rename", _write, f"wiki: rename {page['title']} → {new_title}")
        except StoreError as e:
            return e.response

    def wiki_delete(self, ref: str, force: bool = False, inlink_action: str | None = None) -> dict:
        page = _pages.find_page_by_ref(self._data_root, ref)
        err = self._check_page_accessible(page, ref)
        if err is not None:
            return err

        store = self

        def _write(changed_paths):
            current_page = _pages.find_page_by_ref(store._data_root, ref)
            access_err = store._check_page_accessible(current_page, ref)
            if access_err is not None:
                raise StoreError(access_err)
            title = current_page["title"]
            inlinks = _pages.compute_inlinks(store._data_root, title)
            if inlinks and not force:
                raise StoreError({
                    "code": "DELETE_BLOCKED",
                    "message": f"page has {len(inlinks)} inlink(s); use force=true to delete",
                    "inlinks": inlinks,
                })
            if force and inlinks and inlink_action != "remove_links":
                raise StoreError({
                    "code": "VALIDATION_FAILED",
                    "message": "force delete requires inlink_action='remove_links'",
                    "inlinks": inlinks,
                })

            page_path = _pages.title_to_path(title)
            full = Path(store._data_root) / page_path
            full.unlink()
            changed_paths.append(page_path)

            if inlink_action == "remove_links" and inlinks:
                all_pages = _pages.scan_pages(store._data_root)
                for other_page in all_pages:
                    if other_page.get("_error"):
                        continue
                    new_body = _pages.remove_wikilinks_for_title(other_page["body"], title)
                    if new_body != other_page["body"]:
                        other_path = _pages.title_to_path(other_page["title"])
                        full = Path(store._data_root) / other_path
                        _pages.write_page(str(full), other_page["frontmatter"], new_body)
                        changed_paths.append(other_path)
                        store._search.remove_page(other_page["id"])
                        store._search.index_page(other_page["id"], other_page["title"], new_body)

            store._search.remove_page(current_page["id"])
            return {"id": current_page["id"], "deleted_title": title}

        try:
            return self._mutate("wiki_delete", _write, f"wiki: delete {page['title']}")
        except StoreError as e:
            return e.response

    def wiki_ingest_plan(self, sources: str) -> dict:
        try:
            candidates = json.loads(sources)
        except (json.JSONDecodeError, TypeError):
            return {"code": "VALIDATION_FAILED", "message": "sources must be a JSON array of page objects [{title, body, frontmatter?}]"}
        if not isinstance(candidates, list):
            return {"code": "VALIDATION_FAILED", "message": "sources must be a JSON array of page objects"}

        pages = []
        for c in candidates:
            if not isinstance(c, dict) or "title" not in c or "body" not in c:
                return {"code": "VALIDATION_FAILED", "message": "each page must have title and body"}
            title = c["title"]
            body = c["body"]
            fm = dict(c.get("frontmatter", {}))
            similar = []
            existing = _pages.find_page_by_title(self._data_root, title)
            action = "create"
            if existing is not None:
                action = "skip"
                similar.append({
                    "id": existing.get("id"),
                    "title": title,
                    "score": 1.0,
                    "reason": "exact title match",
                })
            search_resp = self._search.search(f"{title}\n{body[:200]}", top_k=5)
            for r in search_resp.get("results", []):
                p = _pages.find_page_by_id(self._data_root, r["id"])
                if p is None or p["title"] == title:
                    continue
                similar.append({
                    "id": r["id"],
                    "title": p["title"],
                    "score": r["score"],
                    "reason": "similar content",
                })
            pages.append({
                "title": title,
                "body": body,
                "frontmatter": fm,
                "action": action,
                "similar": similar,
            })
        return {
            "pages": pages,
            "base_sha": self._head_sha(),
        }

    def wiki_ingest_apply(self, plan: dict) -> dict:
        pages_list = plan.get("pages", [])
        if not pages_list:
            return {"code": "VALIDATION_FAILED", "message": "plan must contain pages list"}

        seen_titles: set[str] = set()
        for p in pages_list:
            if p.get("action") == "skip":
                continue
            title = p["title"]
            body = p["body"]
            fm = dict(p.get("frontmatter", {}))
            err = self._validate_create_page(title, body, fm, extra_titles=seen_titles)
            if err is not None:
                return err
            seen_titles.add(title)

        store = self

        def _write(changed_paths):
            results = []
            for p in pages_list:
                if p.get("action") == "skip":
                    results.append({"id": p.get("existing_id", p.get("id")), "path": None, "skipped": True})
                    continue
                title = p["title"]
                conflict = store._check_title_exists_locked(title)
                if conflict is not None:
                    raise StoreError(conflict)
                body = p["body"]
                fm = dict(p.get("frontmatter", {}))
                new_id = _pages.make_id(existing_ids={pp["id"] for pp in _pages.scan_pages(store._data_root) if pp["id"]})
                fm["id"] = new_id
                page_path = _pages.title_to_path(title)
                full = Path(store._data_root) / page_path
                _pages.write_page(str(full), fm, body)
                changed_paths.append(page_path)
                store._search.index_page(new_id, title, body)
                results.append({"id": new_id, "path": page_path})
            return {"results": results}

        try:
            return self._mutate("wiki_ingest_apply", _write, "wiki: ingest apply")
        except StoreError as e:
            return e.response

    def wiki_meta_write(self, name: str, content: str) -> dict:
        if name not in ("WIKI.md", "log.md"):
            return {"code": "VALIDATION_FAILED", "message": "name must be WIKI.md or log.md"}

        def _write(changed_paths):
            full = Path(self._data_root) / name
            full.write_text(content, encoding="utf-8")
            changed_paths.append(name)
            return {"name": name, "written": True}

        return self._mutate("wiki_meta_write", _write, f"wiki: meta write {name}")

    def rebuild_index(self) -> dict:
        pages = _pages.scan_pages(self._data_root)
        self._search.rebuild(pages)
        return {"pages_indexed": len(pages), "index_health": self._search.index_health()}

    def pages_count(self) -> int:
        return len(_pages.scan_pages(self._data_root))

    @property
    def search_engine(self) -> _search.WikiSearch:
        return self._search