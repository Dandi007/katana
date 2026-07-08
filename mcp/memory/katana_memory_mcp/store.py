"""存储层纯函数：card 的 frontmatter 解析/序列化与 id 生成。

设计约束（见 work folder design.md）：
- id 为系统身份（m-<6hex>，tenant 内唯一，永不变更）；name 是可读别名兼文件名。
- 序列化保留未知 frontmatter 键（extra），避免 update 丢字段。
"""
import datetime
import glob
import os
import re
import secrets

import yaml

ID_RE = re.compile(r"m-[0-9a-f]{6}")
NAME_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
STATUSES = {"active", "stale", "deprecated"}
TYPES = {"user", "feedback", "project", "reference"}
_CANONICAL = ("id", "name", "description", "status", "last_verified")

# 匹配结束 fence：\n--- 后紧跟换行或文件尾（允许行尾空白）
_FENCE_RE = re.compile(r"\n---[ \t]*(?:\n|$)")


def parse_card(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    m = _FENCE_RE.search(text, 4)
    if m is None:
        return None
    end = m.start()          # \n 的位置
    fence_end = m.end()      # fence 行之后的位置
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
    # 字符类中 `-` 放末尾，避免 `!-` 被解释为字符范围
    if re.search(r'(: )|( #)|^[\s"\'#&*?|>%@`\[\]{},!-]|\s$|^$', v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def serialize_card(meta: dict, body: str) -> str:
    lines = ["---"]
    for k in _CANONICAL:
        if meta.get(k) is not None:
            lines.append(f"{k}: {_scalar(meta[k])}")
    # Build metadata block: type first, then sorted extra keys
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


def gen_id(existing: set[str]) -> str:
    while True:
        i = "m-" + secrets.token_hex(3)
        if i not in existing:
            return i


def _today() -> str:
    return datetime.date.today().isoformat()


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
    for path in sorted(glob.glob(os.path.join(tenant_dir, "*.md"))):
        meta = _read(path)
        if meta is None or not meta.get("id") or not meta.get("name") or not meta.get("description"):
            skipped.append(path)
            continue
        cards.append(meta)
    return cards, skipped


def _l1(meta: dict) -> dict:
    return {k: meta.get(k) for k in ("id", "name", "description", "status", "type", "last_verified")}


def list_cards(tenant_dir: str) -> dict:
    cards, skipped = _scan(tenant_dir)
    return {"cards": [_l1(c) for c in cards], "skipped": skipped}


def _find(tenant_dir: str, card_id: str) -> dict | None:
    cards, _ = _scan(tenant_dir)
    for c in cards:
        if c["id"] == card_id:
            return c
    return None


def get_card(tenant_dir: str, card_id: str) -> dict | None:
    c = _find(tenant_dir, card_id)
    if c is None:
        return None
    out = _l1(c)
    out["body"] = c["body"]
    out["path"] = c["path"]
    return out


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
