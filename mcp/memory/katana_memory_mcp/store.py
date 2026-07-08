"""存储层纯函数：card 的 frontmatter 解析/序列化与 id 生成。

设计约束（见 work folder design.md）：
- id 为系统身份（m-<6hex>，tenant 内唯一，永不变更）；name 是可读别名兼文件名。
- 序列化保留未知 frontmatter 键（extra），避免 update 丢字段。
"""
import re
import secrets

import yaml

ID_RE = re.compile(r"m-[0-9a-f]{6}")
STATUSES = {"active", "stale", "deprecated"}
TYPES = {"user", "feedback", "project", "reference"}
_CANONICAL = ("id", "name", "description", "status", "last_verified")


def parse_card(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    try:
        fm = yaml.safe_load(text[4:end + 1])
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    body = text[end + 4:].lstrip("\n")
    meta = {k: _as_str(fm.pop(k, None)) for k in _CANONICAL}
    metadata = fm.pop("metadata", None) or {}
    meta["type"] = _as_str(metadata.get("type")) if isinstance(metadata, dict) else None
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
        if meta.get(k):
            lines.append(f"{k}: {_scalar(meta[k])}")
    if meta.get("type"):
        lines.append("metadata:")
        lines.append(f"  type: {meta['type']}")
    extra = meta.get("extra") or {}
    if extra:
        lines.append(yaml.safe_dump(extra, allow_unicode=True, sort_keys=True).rstrip("\n"))
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def gen_id(existing: set[str]) -> str:
    while True:
        i = "m-" + secrets.token_hex(3)
        if i not in existing:
            return i
