"""域内检索索引 —— 单个 SQLite 文件，随数据同居一处。

**为什么在域内而不是共享服务**：共享索引器要直接读所有域的文件系统，那是宪法 002
第一条唯一的例外；且无主的共享件会腐烂（旧索引器死了 9 天没人发现，因为没有任何
一个域为它负责）。索引归各域自持后，卷里同时有 git 仓和索引，**自足**——备份、
恢复、搬机器一次覆盖。

**为什么是 SQLite**：单文件天然落进卷；与 kernel 已有的 mutations.sqlite 同栈；
FTS5 自带关键词面，sqlite-vec 提供向量面，两者同库同事务。旧栈的 LanceDB 要拖
pyarrow/lance，且是旧索引器 8.5G 内存峰值的一部分来源。

中文分词：FTS5 的 unicode61 不切 CJK，故用 **trigram** tokenizer（SQLite ≥3.34）。
镜像内实测 `'数据面'` 能命中 `'容器化演练与数据面封仓'`，零额外依赖。

索引位置刻意放 `.katana/runtime/search/`：gitignored（不污染 git 历史），且已被
备份的 runtime 态捕获覆盖。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

INDEX_RELPATH = os.path.join(".katana", "runtime", "search", "index.sqlite")

# 分块：按空行切段再按上限合并。上限取偏小值——bge 类小模型长文本表征会被稀释，
# 且关键词面本来就逐段更准。
_MAX_CHUNK_CHARS = 800
_MIN_CHUNK_CHARS = 40


@dataclass
class Chunk:
    path: str
    ordinal: int
    text: str


def chunk_markdown(text: str) -> list[str]:
    """按空行切段，再贪心合并到 _MAX_CHUNK_CHARS。过短的尾块并入前一块。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= _MAX_CHUNK_CHARS:
            buf = f"{buf}\n{p}"
        else:
            chunks.append(buf)
            buf = p
    if buf:
        if chunks and len(buf) < _MIN_CHUNK_CHARS:
            chunks[-1] = f"{chunks[-1]}\n{buf}"
        else:
            chunks.append(buf)
    return chunks


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SearchIndex:
    """一个域一个实例。线程内使用；跨线程各自开。"""

    def __init__(self, repo_root: str, dim: int | None = None) -> None:
        self.repo_root = repo_root
        self.db_path = os.path.join(repo_root, INDEX_RELPATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.vec_available = self._load_vec()
        self._dim = dim
        self._migrate()

    # -- 建表 ---------------------------------------------------------------

    def _load_vec(self) -> bool:
        """向量扩展是可选项：装不上就退化成纯关键词，不让整个索引不可用。"""
        try:
            import sqlite_vec  # noqa: PLC0415

            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
            self.db.enable_load_extension(False)
            return True
        except Exception:
            return False

    def _migrate(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS docs (
                path        TEXT PRIMARY KEY,
                hash        TEXT NOT NULL,
                indexed_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id       INTEGER PRIMARY KEY,
                path     TEXT NOT NULL REFERENCES docs(path) ON DELETE CASCADE,
                ordinal  INTEGER NOT NULL,
                text     TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            -- trigram：unicode61 不切 CJK，中文查询会整体失灵。
            -- **刻意用独立 FTS5 表而不是 external-content**（content='chunks'）：
            -- 后者的删除必须走 INSERT INTO fts(fts,'delete',...) 特殊语法，普通
            -- DELETE 静默不生效——实测表现为「更新一篇文档后旧内容查不到、新内容
            -- 也查不到」。多存一份文本换掉一整类同步 bug，值。
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(text, path UNINDEXED, tokenize='trigram');
            """
        )
        self.db.commit()

    def _vec_table_exists(self) -> bool:
        """向量表是**懒建**的（首次写入向量时才知道维度），所以任何触碰它的地方
        都要先确认存在——否则 embedding 挂掉期间的删除路径会 OperationalError。"""
        if not self.vec_available:
            return False
        row = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
        ).fetchone()
        return row is not None

    def _ensure_vec_table(self, dim: int) -> None:
        if not self.vec_available:
            return
        if self._dim is None:
            self._dim = dim
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])"
        )
        self.db.commit()

    # -- 写 -----------------------------------------------------------------

    def needs_reindex(self, path: str, text: str) -> bool:
        row = self.db.execute("SELECT hash FROM docs WHERE path=?", (path,)).fetchone()
        return row is None or row["hash"] != content_hash(text)

    def remove(self, path: str) -> None:
        ids = [r["id"] for r in self.db.execute("SELECT id FROM chunks WHERE path=?", (path,))]
        has_vec = self._vec_table_exists()
        for cid in ids:
            self.db.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
            if has_vec:
                self.db.execute("DELETE FROM chunks_vec WHERE rowid=?", (cid,))
        self.db.execute("DELETE FROM chunks WHERE path=?", (path,))
        self.db.execute("DELETE FROM docs WHERE path=?", (path,))
        self.db.commit()

    def upsert(self, path: str, text: str, vectors: list[list[float]] | None = None) -> int:
        """重建该 path 的全部分块。`vectors` 为 None 时只建关键词面。

        向量面缺失不是错误——embedding 端点挂了时索引仍要能继续推进，等端点恢复
        后由 reindex 补齐。内容是权威，向量是派生物。
        """
        import time  # noqa: PLC0415

        self.remove(path)
        chunks = chunk_markdown(text)
        if vectors is not None and len(vectors) != len(chunks):
            raise ValueError(f"向量数 {len(vectors)} 与分块数 {len(chunks)} 不符")
        if vectors:
            self._ensure_vec_table(len(vectors[0]))
        self.db.execute(
            "INSERT INTO docs(path, hash, indexed_at) VALUES (?,?,?)",
            (path, content_hash(text), time.time()),
        )
        for i, ch in enumerate(chunks):
            cur = self.db.execute(
                "INSERT INTO chunks(path, ordinal, text) VALUES (?,?,?)", (path, i, ch)
            )
            cid = cur.lastrowid
            self.db.execute(
                "INSERT INTO chunks_fts(rowid, text, path) VALUES (?,?,?)", (cid, ch, path)
            )
            if vectors is not None and self.vec_available:
                self.db.execute(
                    "INSERT INTO chunks_vec(rowid, embedding) VALUES (?,?)",
                    (cid, _pack(vectors[i])),
                )
        self.db.commit()
        return len(chunks)

    # -- 读 -----------------------------------------------------------------

    def all_paths(self) -> list[str]:
        return [r["path"] for r in self.db.execute("SELECT path FROM docs")]

    # trigram 以 3 字符为最小索引单元 ⇒ **query 短于 3 字符时 FTS 恒不命中**。
    # 中文双字词（检索/封仓/容器/设计…）极常见，实测 '检索' 在含「关于检索」的
    # 文档上命中 0。故短 query 走 LIKE 子串扫描兜底：域内语料规模下可接受，
    # 且只在短 query 时触发。
    _TRIGRAM_MIN = 3

    def keyword_search(self, query: str, top_k: int) -> list[dict]:
        """关键词检索：精确短语（FTS）优先，再并上按词 AND 的子串命中。

        为什么不是单纯把整个 query 丢给 FTS：
        - trigram 以 3 字符为最小单元 ⇒ **短于 3 字符的 query 恒不命中**，而中文
          双字词（检索/封仓/内容…）极常见（实测 '检索' 在含「关于检索」的文档上
          命中 0）；
        - 整个 query 当精确短语时，`MCP 治理` 这种多词查询要求原文逐字连续出现，
          真实语料上实测返回空——而这是最常见的查询形状之一。

        故：短语命中排前（精确性最高），按词 AND 的子串命中补后（召回）。
        """
        terms = [t for t in query.split() if t]
        results: dict[str, dict] = {}

        # 1) 精确短语：只有 query 够长才可能命中
        if len(query.strip()) >= self._TRIGRAM_MIN:
            q = '"' + query.replace('"', " ") + '"'
            try:
                rows = self.db.execute(
                    """
                    SELECT path, text, bm25(chunks_fts) AS score
                    FROM chunks_fts
                    WHERE chunks_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (q, top_k * 5),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for r in rows:
                results.setdefault(
                    r["path"], {"path": r["path"], "text": r["text"], "score": r["score"]}
                )

        # 2) 按词 AND 的子串命中（覆盖短词与多词两种 FTS 打不中的形状）
        if len(results) < top_k and terms:
            for r in self._like_and_search(terms, top_k):
                results.setdefault(r["path"], r)

        return list(results.values())[:top_k]

    @staticmethod
    def _escape_like(term: str) -> str:
        return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _like_and_search(self, terms: list[str], top_k: int) -> list[dict]:
        """子串扫描：**所有**词都出现才算命中。按块更短排序（更聚焦）。"""
        if not terms:
            return []
        where = " AND ".join(["text LIKE '%' || ? || '%' ESCAPE '\\'"] * len(terms))
        rows = self.db.execute(
            f"SELECT path, text FROM chunks WHERE {where} ORDER BY LENGTH(text) LIMIT ?",
            (*[self._escape_like(t) for t in terms], top_k * 5),
        ).fetchall()
        seen: dict[str, dict] = {}
        for r in rows:
            if r["path"] not in seen:
                seen[r["path"]] = {"path": r["path"], "text": r["text"], "score": 0.0}
        return list(seen.values())[:top_k]

    def vector_search(self, vector: list[float], top_k: int) -> list[dict]:
        if not self._vec_table_exists():
            return []
        try:
            rows = self.db.execute(
                """
                SELECT c.path AS path, c.text AS text, v.distance AS distance
                FROM chunks_vec v JOIN chunks c ON c.id = v.rowid
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (_pack(vector), top_k * 5),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        seen: dict[str, dict] = {}
        for r in rows:
            if r["path"] not in seen:
                seen[r["path"]] = {"path": r["path"], "text": r["text"], "score": -r["distance"]}
        return list(seen.values())[:top_k]

    def stats(self) -> dict:
        docs = self.db.execute("SELECT COUNT(*) c FROM docs").fetchone()["c"]
        chunks = self.db.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        vecs = 0
        if self._vec_table_exists():
            try:
                vecs = self.db.execute("SELECT COUNT(*) c FROM chunks_vec").fetchone()["c"]
            except sqlite3.OperationalError:
                vecs = 0
        return {
            "docs": docs,
            "chunks": chunks,
            "vectors": vecs,
            "vector_backend": "sqlite-vec" if self.vec_available else "unavailable",
            "db_path": INDEX_RELPATH,
        }

    def close(self) -> None:
        self.db.close()
