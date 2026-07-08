"""一次性迁移：把存量 memory card 拷入 tenant 目录并补 id。

唯一内容改动 = 在 frontmatter 首行 `---` 之后插入 `id: m-xxxxxx`；
其余字节保持原样（存量卡 frontmatter 含非 canonical 字段，不做重序列化）。

CRLF 处理决策：
- body 含 CRLF（frontmatter 为 LF）：binary 写出保证字节 100% 原样。
- frontmatter 本身为 CRLF（`---\r\n` 开头）：parse_card 返回 None → skipped，
  需人工处理。
"""
import argparse
import glob
import os
import shutil

from katana_memory_mcp import store


def _existing_ids(*dirs: str) -> set[str]:
    ids: set[str] = set()
    for d in dirs:
        for p in glob.glob(os.path.join(d, "*.md")):
            try:
                with open(p, "rb") as f:
                    raw = f.read()
                meta = store.parse_card(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if meta and meta.get("id"):
                ids.add(meta["id"])
    return ids


def migrate(src_dirs: list[str], dest_dir: str) -> dict:
    os.makedirs(dest_dir, exist_ok=True)
    ids = _existing_ids(dest_dir, *src_dirs)
    migrated, skipped, collisions = 0, [], []
    for src in src_dirs:
        for path in sorted(glob.glob(os.path.join(src, "*.md"))):
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append(path)
                continue
            meta = store.parse_card(text)
            if meta is None or not meta.get("name") or not meta.get("description"):
                skipped.append(path)
                continue
            dest = os.path.join(dest_dir, os.path.basename(path))
            if os.path.exists(dest):
                collisions.append(path)
                continue
            if meta.get("id"):
                shutil.copyfile(path, dest)
            else:
                if not raw.startswith(b"---\n"):
                    # frontmatter 本身是 CRLF 或其他异常前缀，跳过
                    skipped.append(path)
                    continue
                new_id = store.gen_id(ids)
                ids.add(new_id)
                with open(dest, "wb") as f:
                    f.write(b"---\nid: " + new_id.encode() + b"\n" + raw[4:])
            migrated += 1
    return {"migrated": migrated, "skipped": skipped, "collisions": collisions}


def main() -> None:
    ap = argparse.ArgumentParser(description="migrate legacy memory cards into a tenant dir")
    ap.add_argument("src", nargs="+", help="source dirs")
    ap.add_argument("--dest", required=True, help="tenant dir, e.g. /data/memory/uther")
    args = ap.parse_args()
    print(migrate(args.src, args.dest))


if __name__ == "__main__":
    main()
