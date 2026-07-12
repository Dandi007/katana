"""存储层：MemoryStore（governed） + 模块级 legacy 测试 helper。

MemoryStore 经 kernel binding 的 VFS 执行所有 I/O，无 raw fs 操作。
模块级函数为 legacy 测试 helper，不用于生产路径。
"""

import datetime
import os
import re
import secrets

import yaml

from katana_kernel.kernel import DomainBinding, GovernedKernel

ID_RE = re.compile(r"m-[0-9a-f]{6}")
NAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
STATUSES = {"active", "stale", "deprecated"}
TYPES = {"user", "feedback", "project", "reference"}
_CANONICAL = ("id", "name", "description", "status", "last_verified")

_FENCE_RE = re.compile(r"\n---[ \t]*(?:\n|$)")


def parse_card(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    m = _FENCE_RE.search(text, 4)
    if m is None:
        return None
    end = m.start()
    fence_end = m.end()
    try:
        fm = yaml.safe_load(text[4:end + 1])
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    body = text[fence_end:].lstrip("\n")
    meta = {k: _as_str(fm.pop(k, None)) for k in _CANONICAL}
    metadata = fm.pop("metadata", None) or {}
    if isinstance(metadata, dict):
        meta["type"] = _as_str(metadata.get("type"))
        extra_keys = {k: v for k, v in metadata.items() if k != "type"}
        meta["metadata_extra"] = extra_keys if extra_keys else {}
    else:
        meta["type"] = None
        meta["metadata_extra"] = {}
    meta["extra"] = fm
    meta["body"] = body
    return meta


def _as_str(v) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def _scalar(v: str) -> str:
    if re.search(r'(: )|( #)|^[\s"\'#&*?|>%@`\[\]{},!-]|\s$|^$', v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def serialize_card(meta: dict, body: str) -> str:
    lines = ["---"]
    for k in _CANONICAL:
        if meta.get(k) is not None:
            lines.append(f"{k}: {_scalar(meta[k])}")
    mtype = meta.get("type")
    mextra = meta.get("metadata_extra") or {}
    if mtype is not None or mextra:
        lines.append("metadata:")
        if mtype is not None:
            lines.append(f"  type: {mtype}")
        for k in sorted(mextra.keys()):
            v = mextra[k]
            if isinstance(v, bool):
                lines.append(f"  {k}: {str(v).lower()}")
            elif v is None:
                lines.append(f"  {k}: null")
            elif isinstance(v, str):
                lines.append(f"  {k}: {_scalar(v)}")
            else:
                lines.append(f"  {k}: {v}")
    extra = meta.get("extra") or {}
    if extra:
        lines.append(yaml.safe_dump(extra, allow_unicode=True, sort_keys=True).rstrip("\n"))
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.rstrip("\n") + "\n"


def _today() -> str:
    return datetime.date.today().isoformat()


def _l1(meta: dict) -> dict:
    return {k: meta.get(k) for k in ("id", "name", "description", "status", "type", "last_verified")}


def _validate(name=None, status=None, type=None, description=None, last_verified=None) -> None:
    if name is not None and not NAME_RE.fullmatch(name):
        raise ValueError(f"invalid name (kebab-case required): {name!r}")
    if status is not None and status not in STATUSES:
        raise ValueError(f"invalid status: {status!r} (allowed: {sorted(STATUSES)})")
    if type is not None and type not in TYPES:
        raise ValueError(f"invalid type: {type!r} (allowed: {sorted(TYPES)})")
    if description is not None and "\n" in description:
        raise ValueError("description must be a single line (no newlines)")
    if last_verified is not None and "\n" in last_verified:
        raise ValueError("last_verified must be a single line (no newlines)")


# ── MemoryStore (governed, production path) ──────────────────────────────────

class MemoryStore:
    def __init__(self, kernel: GovernedKernel):
        self._kernel = kernel
        self._binding = kernel.get_binding("memory")

    def _card_path(self, tenant: str, name: str) -> str:
        return f"{tenant}/{name}.md"

    def _scan(self, tenant: str) -> tuple[list[dict], list[str]]:
        cards, skipped = [], []
        prefix = f"{tenant}/"
        for p in self._binding.vfs.ls(f"{tenant}/*.md"):
            try:
                text = self._binding.vfs.read_text(p)
            except Exception:
                skipped.append(p)
                continue
            meta = parse_card(text)
            if meta is None or not meta.get("id") or not meta.get("name") or not meta.get("description"):
                skipped.append(p)
                continue
            meta["path"] = p
            cards.append(meta)
        return cards, skipped

    def _find(self, tenant: str, card_id: str) -> dict | None:
        cards, _ = self._scan(tenant)
        for c in cards:
            if c["id"] == card_id:
                return c
        return None

    def _existing_ids(self, tenant: str) -> set[str]:
        cards, _ = self._scan(tenant)
        return {c["id"] for c in cards}

    def list_cards(self, tenant: str) -> dict:
        cards, skipped = self._scan(tenant)
        return {"cards": [_l1(c) for c in cards], "skipped": skipped}

    def get_card(self, tenant: str, card_id: str) -> dict | None:
        c = self._find(tenant, card_id)
        if c is None:
            return None
        out = _l1(c)
        out["body"] = c["body"]
        out["path"] = c["path"]
        return out

    def create_card(self, tenant: str, name: str, description: str, body: str,
                    type: str | None = None, now: str | None = None,
                    expected_base_sha: str | None = None) -> dict:
        _validate(name=name, type=type, description=description)
        if not description.strip():
            raise ValueError("description is required")
        card_path = self._card_path(tenant, name)
        if self._binding.vfs.exists(card_path):
            raise ValueError(f"card name already exists: {name}")

        store = self

        def _write(binding, args):
            new_id = binding.ledger.gen_id(store._existing_ids(tenant))
            meta = {
                "id": new_id, "name": name, "description": description,
                "status": "active", "last_verified": now or _today(),
                "type": type, "extra": {}, "metadata_extra": {},
            }
            content = serialize_card(meta, body)
            binding.vfs.write(card_path, content, op="create", args=args)
            out = _l1(meta)
            out["changed_paths"] = [card_path]
            return out

        return self._call_mutate("create", {
            "name": name, "description": description, "body": body, "type": type, "tenant": tenant,
        }, _write, expected_base_sha,
            f"chore(memory): [{tenant}] create {name}")

    def update_card(self, tenant: str, card_id: str, *, name: str | None = None,
                    description: str | None = None, body: str | None = None,
                    status: str | None = None, type: str | None = None,
                    last_verified: str | None = None,
                    expected_base_sha: str | None = None) -> dict:
        _validate(name=name, status=status, type=type, description=description, last_verified=last_verified)
        if description is not None and not description:
            raise ValueError("description cannot be empty string (use None to leave unchanged)")
        cur = self._find(tenant, card_id)
        if cur is None:
            raise KeyError(f"card not found: {card_id}")
        old_path = cur["path"]
        old_name = cur["name"]

        store = self

        def _write(binding, args):
            changed = [old_path]
            for k, v in (("name", name), ("description", description), ("status", status),
                         ("type", type), ("last_verified", last_verified)):
                if v is not None:
                    cur[k] = v
            new_body = body if body is not None else cur["body"]
            new_path = store._card_path(tenant, cur["name"])
            if new_path != old_path:
                if binding.vfs.exists(new_path):
                    raise ValueError(f"card name already exists: {cur['name']}")
                binding.vfs.rename(old_path, new_path, op="update", args=args)
                changed.append(new_path)
            binding.vfs.write(new_path, serialize_card(cur, new_body), op="update", args=args)
            out = _l1(cur)
            out["changed_paths"] = changed
            return out

        return self._call_mutate("update", {
            "id": card_id, "name": name, "description": description, "body": body,
            "status": status, "type": type, "last_verified": last_verified, "tenant": tenant,
        }, _write, expected_base_sha,
            f"chore(memory): [{tenant}] update {card_id} ({cur['name']})")

    def delete_card(self, tenant: str, card_id: str,
                    expected_base_sha: str | None = None) -> dict:
        cur = self._find(tenant, card_id)
        if cur is None:
            raise KeyError(f"card not found: {card_id}")
        card_path = cur["path"]

        def _write(binding, args):
            binding.vfs.delete(card_path, op="delete", args=args)
            out = _l1(cur)
            out["changed_paths"] = [card_path]
            return out

        return self._call_mutate("delete", {
            "id": card_id, "tenant": tenant,
        }, _write, expected_base_sha,
            f"chore(memory): [{tenant}] delete {card_id} ({cur['name']})")

    def read_card_raw(self, tenant: str, card_id: str, *, offset: int | None = None,
                      limit: int | None = None) -> dict:
        cur = self._find(tenant, card_id)
        if cur is None:
            raise KeyError(f"card not found: {card_id}")
        text = self._binding.vfs.read_text(cur["path"])
        lines = text.split("\n")
        total = len(lines)
        start = max(1, offset or 1)
        last = min(total, start + limit - 1) if limit is not None else total
        if start > total or start > last:
            rendered = ""
        else:
            rendered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, last + 1))
        return {
            "id": card_id, "name": cur["name"], "total_lines": total,
            "offset": start, "limit": limit, "content": rendered,
        }

    def edit_card(self, tenant: str, card_id: str, old_string: str, new_string: str,
                  *, replace_all: bool = False,
                  expected_base_sha: str | None = None) -> dict:
        if not old_string:
            raise ValueError("old_string must be non-empty")
        if old_string == new_string:
            raise ValueError("old_string must differ from new_string")
        cur = self._find(tenant, card_id)
        if cur is None:
            raise KeyError(f"card not found: {card_id}")
        old_path = cur["path"]
        text = self._binding.vfs.read_text(old_path)
        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"old_string not found in card {card_id}")
        if count > 1 and not replace_all:
            raise ValueError(
                f"old_string matches {count} times in card {card_id}; "
                "narrow it or pass replace_all=True"
            )
        new_text = text.replace(old_string, new_string) if replace_all \
            else text.replace(old_string, new_string, 1)
        parsed = parse_card(new_text)
        if parsed is None or not parsed.get("id") or not parsed.get("name") \
                or not parsed.get("description"):
            raise ValueError("edit would produce an unparseable/invalid card; aborted (no write)")
        if parsed["id"] != card_id:
            raise ValueError("id is immutable; edits that change the id field are rejected")
        _validate(name=parsed["name"], status=parsed["status"], type=parsed["type"],
                  description=parsed["description"], last_verified=parsed["last_verified"])

        store = self

        def _write(binding, args):
            new_path = store._card_path(tenant, parsed["name"])
            if new_path != old_path and binding.vfs.exists(new_path):
                raise ValueError(f"card name already exists: {parsed['name']}")
            binding.vfs.write(old_path, new_text, op="edit", args=args)
            changed = [old_path]
            if new_path != old_path:
                binding.vfs.rename(old_path, new_path, op="edit", args=args)
                changed.append(new_path)
            out = _l1(parsed)
            out["changed_paths"] = changed
            return out

        return self._call_mutate("edit", {
            "id": card_id, "old_string": old_string, "new_string": new_string,
            "body": new_text, "tenant": tenant,
        }, _write, expected_base_sha,
            f"chore(memory): [{tenant}] edit {card_id} ({cur['name']})")

    def _call_mutate(self, op: str, args: dict, write_fn, expected_base_sha: str | None,
                     commit_msg: str) -> dict:
        return self._kernel.mutate(
            "memory", op, args,
            expected_base_sha=expected_base_sha,
            write_fn=write_fn,
            commit_msg=commit_msg,
        )


# ── Legacy test helpers (module-level, for backward compatibility with store tests) ──

import glob as _glob_module


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            meta = parse_card(f.read())
    except OSError:
        return None
    if meta is not None:
        meta["path"] = path
    return meta


def _scan(tenant_dir: str) -> tuple[list[dict], list[str]]:
    cards, skipped = [], []
    for path in sorted(_glob_module.glob(os.path.join(tenant_dir, "*.md"))):
        meta = _read(path)
        if meta is None or not meta.get("id") or not meta.get("name") or not meta.get("description"):
            skipped.append(path)
            continue
        cards.append(meta)
    return cards, skipped


def _find(tenant_dir: str, card_id: str) -> dict | None:
    cards, _ = _scan(tenant_dir)
    for c in cards:
        if c["id"] == card_id:
            return c
    return None


def gen_id(existing: set[str]) -> str:
    while True:
        i = "m-" + secrets.token_hex(3)
        if i not in existing:
            return i


def list_cards(tenant_dir: str) -> dict:
    cards, skipped = _scan(tenant_dir)
    return {"cards": [_l1(c) for c in cards], "skipped": skipped}


def get_card(tenant_dir: str, card_id: str) -> dict | None:
    c = _find(tenant_dir, card_id)
    if c is None:
        return None
    out = _l1(c)
    out["body"] = c["body"]
    out["path"] = c["path"]
    return out


def create_card(tenant_dir: str, name: str, description: str, body: str,
                type: str | None = None, now: str | None = None) -> dict:
    _validate(name=name, type=type, description=description)
    if not description.strip():
        raise ValueError("description is required")
    path = os.path.join(tenant_dir, f"{name}.md")
    if os.path.exists(path):
        raise ValueError(f"card name already exists: {name}")
    cards, _ = _scan(tenant_dir)
    meta = {
        "id": gen_id({c["id"] for c in cards}),
        "name": name, "description": description,
        "status": "active", "last_verified": now or _today(),
        "type": type, "extra": {}, "metadata_extra": {},
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(serialize_card(meta, body))
    out = _l1(meta)
    out["changed_paths"] = [path]
    return out


def update_card(tenant_dir: str, card_id: str, *, name: str | None = None,
                description: str | None = None, body: str | None = None,
                status: str | None = None, type: str | None = None,
                last_verified: str | None = None) -> dict:
    _validate(name=name, status=status, type=type, description=description, last_verified=last_verified)
    if description is not None and not description:
        raise ValueError("description cannot be empty string (use None to leave unchanged)")
    cur = _find(tenant_dir, card_id)
    if cur is None:
        raise KeyError(f"card not found: {card_id}")
    old_path = cur["path"]
    changed = [old_path]
    for k, v in (("name", name), ("description", description), ("status", status),
                 ("type", type), ("last_verified", last_verified)):
        if v is not None:
            cur[k] = v
    new_body = body if body is not None else cur["body"]
    new_path = os.path.join(tenant_dir, f"{cur['name']}.md")
    if new_path != old_path:
        if os.path.exists(new_path):
            raise ValueError(f"card name already exists: {cur['name']}")
        os.rename(old_path, new_path)
        changed.append(new_path)
    with open(new_path, "w", encoding="utf-8") as f:
        f.write(serialize_card(cur, new_body))
    out = _l1(cur)
    out["changed_paths"] = changed
    return out


def delete_card(tenant_dir: str, card_id: str) -> dict:
    cur = _find(tenant_dir, card_id)
    if cur is None:
        raise KeyError(f"card not found: {card_id}")
    os.remove(cur["path"])
    out = _l1(cur)
    out["changed_paths"] = [cur["path"]]
    return out


def read_card_raw(tenant_dir: str, card_id: str, *, offset: int | None = None,
                  limit: int | None = None) -> dict:
    cur = _find(tenant_dir, card_id)
    if cur is None:
        raise KeyError(f"card not found: {card_id}")
    with open(cur["path"], encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    total = len(lines)
    start = max(1, offset or 1)
    last = min(total, start + limit - 1) if limit is not None else total
    if start > total or start > last:
        rendered = ""
    else:
        rendered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, last + 1))
    return {
        "id": card_id, "name": cur["name"], "total_lines": total,
        "offset": start, "limit": limit, "content": rendered,
    }


def edit_card(tenant_dir: str, card_id: str, old_string: str, new_string: str,
              *, replace_all: bool = False) -> dict:
    if not old_string:
        raise ValueError("old_string must be non-empty")
    if old_string == new_string:
        raise ValueError("old_string must differ from new_string")
    cur = _find(tenant_dir, card_id)
    if cur is None:
        raise KeyError(f"card not found: {card_id}")
    old_path = cur["path"]
    with open(old_path, encoding="utf-8") as f:
        text = f.read()
    count = text.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in card {card_id}")
    if count > 1 and not replace_all:
        raise ValueError(
            f"old_string matches {count} times in card {card_id}; "
            "narrow it or pass replace_all=True"
        )
    new_text = text.replace(old_string, new_string) if replace_all \
        else text.replace(old_string, new_string, 1)
    parsed = parse_card(new_text)
    if parsed is None or not parsed.get("id") or not parsed.get("name") \
            or not parsed.get("description"):
        raise ValueError("edit would produce an unparseable/invalid card; aborted (no write)")
    if parsed["id"] != card_id:
        raise ValueError("id is immutable; edits that change the id field are rejected")
    _validate(name=parsed["name"], status=parsed["status"], type=parsed["type"],
              description=parsed["description"], last_verified=parsed["last_verified"])
    new_path = os.path.join(tenant_dir, f"{parsed['name']}.md")
    if new_path != old_path and os.path.exists(new_path):
        raise ValueError(f"card name already exists: {parsed['name']}")
    with open(old_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    changed = [old_path]
    if new_path != old_path:
        os.rename(old_path, new_path)
        changed.append(new_path)
    out = _l1(parsed)
    out["changed_paths"] = changed
    return out