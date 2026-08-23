"""katana-wiki-mcp — wiki 的 FastMCP server。

Tools:
  wiki_search — 薄检索原语，复用 vault-search 栈，按 wiki_root scope。
  wiki_query  — fat tool：判重检索 + cold gap-log + 返回综合协议文本。
业务逻辑抽成纯函数便于单测；FastMCP tool 只做薄壳。

多租户：`WikiService` 承载单租户全部状态，`build_tenant_server()` 为一个租户
构建独立 FastMCP 实例，`build_app(tenant_map)` 把多个租户挂载在
``/t/<tenant>/mcp``（tenant 由 URL 绑定，不进 tool 签名——与 memory 同构）。
模块级 `mcp` + `configure()` 是单租户兼容面，行为不变。
"""
import contextlib
import datetime
import json
import os
import re
from pathlib import Path

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

from katana_kb_mcp_shared import config, vault_search
from katana_kernel import (
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    MutationBrokenError,
    ResourceIdLedger,
    TransactionManifest,
    head_sha,
)
from katana_wiki_mcp import enumerate as _enumerate
from katana_wiki_mcp import ingest as _ingest
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp import lint as _lint
from katana_wiki_mcp import pages as _pages
from katana_wiki_mcp import query as _query
from katana_wiki_mcp.store import WikiStore, _wiki_policy
from katana_wiki_mcp.fs_tools import FSTools

_INSTRUCTIONS = (
    "Wiki 的 MCP 接口：知识检索/查询/入库。"
    "wiki_search 做混合检索并返回带路径的候选，agent 可据路径自行深挖。"
    "wiki_query 做 fat 检索：判重 + cold gap-log + 综合协议，是 query skill 的 server 侧。"
    "fs_* 工具提供 Full VFS 读写面：fs_resolve/fs_stat/fs_list/fs_glob/fs_read 做发现与读取；"
    "fs_create/fs_write/fs_edit/fs_copy/fs_rename/fs_delete 做受治理的 mutation；"
    "fs_batch 做单 repo all-or-nothing 批量事务。"
)


def _server_mutation(call) -> dict:
    try:
        return call()
    except MutationBrokenError as exc:
        return exc.as_error()


def compute_scope(wiki_root: str, kb_root: str) -> str | None:
    """wiki_root 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(wiki_root, kb_root)
    return None if rel in (".", "") else rel


class WikiService:
    """单租户 wiki 的全部状态与业务操作（scope、治理 kernel、store、fs）。"""

    def __init__(self, wiki_root: str, kb_root: str) -> None:
        self.wiki_root = wiki_root
        self.kb_root = kb_root
        self.scope = compute_scope(wiki_root, kb_root)
        self.kernel: GovernedKernel | None = None
        self.store: WikiStore | None = None
        self.fs_tools: FSTools | None = None
        if os.path.isdir(wiki_root):
            self.kernel = GovernedKernel()
            vfs = GovernedVFS(wiki_root)
            ledger = ResourceIdLedger(
                os.path.join(wiki_root, ".katana", "tombstones.json"),
                prefix="w-",
            )
            manifest = TransactionManifest(os.path.join(wiki_root, ".katana", "manifests"))
            self.kernel.bind("wiki", _wiki_policy(), vfs, ledger, manifest, wiki_root)
            self.store = WikiStore(self.kernel)
            self.fs_tools = FSTools(self.kernel, wiki_root)

    # ── guards ──
    def require_store(self) -> WikiStore:
        if self.store is None:
            raise RuntimeError("wiki store not initialized; call configure() first")
        return self.store

    def require_fs_tools(self) -> FSTools:
        if self.fs_tools is None:
            raise RuntimeError("wiki fs_tools not initialized; call configure() first")
        return self.fs_tools

    # ── search ──
    def is_wiki_path(self, path: str) -> bool:
        """Whether `path` (kb_root-relative) resolves to a real file in the wiki repo."""
        if not self.wiki_root or not path:
            return False
        candidate = os.path.normpath(os.path.join(self.wiki_root, path))
        root = os.path.normpath(self.wiki_root)
        if not (candidate == root or candidate.startswith(root + os.sep)):
            return False
        return os.path.isfile(candidate)

    def do_search(self, query: str, top_k: int) -> list[dict]:
        # vault-search indexes several roots (wiki + work-records + vault) into one
        # index, and `dir` only scopes to a single prefix. When wiki_root == kb_root
        # the computed scope is None, so unfiltered results would include paths from
        # other domains that this server's VFS cannot even open (RESOURCE_NOT_FOUND).
        # Over-fetch, then keep only paths that actually exist in the wiki repo.
        scope = self.scope
        fetch_k = top_k if scope else max(top_k * 4, 20)
        resp = vault_search.search(query, top_k=fetch_k, dir=scope)
        out: list[dict] = []
        for r in resp.results:
            if not scope and not self.is_wiki_path(r.path):
                continue
            out.append(
                {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
            )
            if len(out) >= top_k:
                break
        return out

    def scoped_search(self, query: str, *, top_k: int = 10, dir: str | None = None, **kwargs):
        """vault_search.search, with cross-domain results dropped.

        Injected into query/ingest so their dedup candidates obey the same wiki-only
        boundary as wiki_search — otherwise `cold` and create-vs-update decisions are
        made against pages from other domains.
        """
        fetch_k = top_k if dir else max(top_k * 4, 20)
        resp = vault_search.search(query, top_k=fetch_k, dir=dir, **kwargs)
        if not dir:
            resp.results = [r for r in resp.results if self.is_wiki_path(r.path)]
        del resp.results[top_k:]
        return resp

    # ── gap log ──
    def governed_append_log(self, wiki_root: str, line: str) -> None:
        if self.store is not None:
            self.store.append_gap_log(line)
        else:
            _pages.append_log(wiki_root, line)


def register_tools(m: FastMCP, svc) -> dict:
    """在 FastMCP 实例上注册全套 wiki tools，全部委托给 `svc`。

    `svc` 可以是 WikiService，也可以是延迟解析的 proxy（模块级兼容面）。
    返回 {tool_name: tool_object}。
    """

    @m.tool()
    async def wiki_search(query: str, top_k: int = 10) -> list[dict]:
        """对 wiki 做混合检索（RRF：关键词+向量），返回带路径的候选。

        返回每条含 path/score/title/snippet。拿到 path 后可自行 read 全文 / grep / 顺 wikilink 深挖——
        本 tool 不替你嚼碎，保留你的自由探索。

        Args:
            query: 检索词或自然语言查询。
            top_k: 返回上限，默认 10。
        """
        return svc.do_search(query, top_k)

    @m.tool()
    async def wiki_query(question: str, top_k: int = 10) -> dict:
        """对 wiki 提问：server 判重检索 + 返回候选(带路径)与综合协议；空集走 cold 并记 gap log。

        返回 candidates 后，按 synthesis_contract 综合作答：每条 claim 带 citation 或标 [inference]；
        candidates 带 path，可自行 read 全文深挖。cold=True 表示 wiki 不覆盖，勿裸答冒充。

        Args:
            question: 问题文本。
            top_k: 候选上限，默认 10。
        """
        return _server_mutation(
            lambda: _query._do_query(
                question, svc.scope, svc.wiki_root or ".", top_k,
                search_fn=svc.scoped_search,
                log_fn=svc.governed_append_log,
                now_fn=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            )
        )

    @m.tool()
    async def wiki_report_gap(question: str, note: str | None = None) -> dict:
        """记录一次「wiki 未覆盖」——当 wiki_query 返回了候选但你自检后判定无一支撑本问题时调用。

        cold=False 只代表检索有返回。分数不能区分真命中与噪声（标题字面匹配得高分，
        自然语言提问命中真页面时分数与无关页同量级），所以「是否真覆盖」须由你阅读候选后判断；
        判为未覆盖时用本 tool 记 gap log，让知识盲区可见——否则 gap 只在结果集为空时才被记录。

        Args:
            question: 原始问题文本。
            note: 可选，补充说明（例如最接近的候选为何不够）。
        """
        store = svc.require_store()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"## [{ts}] query | gap: {question}"
        if note:
            line += f" — {note}"
        return _server_mutation(lambda: store.append_gap_log(line))

    @m.tool()
    async def wiki_ingest_plan(source_text: str) -> dict:
        """入库第一步：server orient 判重，并返回 proposal schema 与唯一 canonical base_sha。
        updates apply 必须传 expected_base_sha=plan.base_sha。Args: source_text 待入库内容(或其摘录)。"""
        return _ingest.plan(
            source_text,
            svc.scope,
            search_fn=svc.scoped_search,
            base_sha=head_sha(svc.wiki_root or "."),
        )

    @m.tool()
    async def wiki_ingest_apply(proposal: dict,
                                expected_base_sha: str | None = None) -> dict:
        """入库第二步：server 校验 create/update 意图及页面不变量；existing page 仅允许 updates 且必须保留 path/id，任何歧义均拒绝并零落盘。通过后经 kernel 治理链写页+自动反链+log+commit。
        Args: proposal 见 wiki_ingest_plan 返回的 proposal_schema。updates 非空时 expected_base_sha 必须使用 plan 返回值。返回 applied/rejected/commit。"""
        store = svc.require_store()
        return _server_mutation(
            lambda: store.ingest_apply(
                proposal, expected_base_sha=expected_base_sha,
            )
        )

    @m.tool()
    async def wiki_list_docs(zone: str | None = None) -> list[dict]:
        """枚举 wiki 全部可写文档（自动排除 raw/干扰目录），返回带路径的清单。

        返回每条含 path/类型/frontmatter/mtime/hash。拿到 path 后可自行 read/grep/顺 wikilink 深挖。
        Args: zone 可选，限定子目录前缀（如 "DeepThought"）。
        """
        docs = _enumerate.enumerate_docs(svc.wiki_root or ".")
        if zone:
            docs = [d for d in docs if d["path"].startswith(zone.rstrip("/") + "/")]
        return docs

    @m.tool()
    async def wiki_lint_mechanical(path: str | None = None, zone: str | None = None,
                                   offset: int = 0, limit: int | None = 200) -> dict:
        """确定性机械体检：逐页不变量（缺 provenance/outlink/摘要/frontmatter）+ 跨页 orphan/broken_link。

        返回 {findings, skipped, scanned, total_findings, by_code, affected_pages, offset, truncated}。
        raw zone 自动豁免；若 zone 落在排除区，skipped 会说明「未做检查」而非静默报干净。
        Args: path 可选，限定单页逐页检查（跨页基线仍扫全 zone）；zone 可选，限定子目录前缀（如 "DeepThought"），跨页基线只在该 zone 内算；
          offset/limit 对 findings 分页（默认每页 200；全库 findings 可达数千条，勿一次全取）。先看 by_code 汇总再按需翻页。
        """
        return _lint.lint_mechanical(svc.wiki_root or ".", path, zone=zone,
                                     offset=offset, limit=limit)

    # ── fs_* Full VFS tools ──────────────────────────────────────────────

    @m.tool()
    async def fs_capabilities() -> dict:
        return svc.require_fs_tools().fs_capabilities()

    @m.tool()
    async def fs_resolve(path_or_id: str) -> dict:
        return svc.require_fs_tools().fs_resolve(path_or_id)

    @m.tool()
    async def fs_stat(path: str) -> dict:
        return svc.require_fs_tools().fs_stat(path)

    @m.tool()
    async def fs_list(path: str = "") -> dict:
        return svc.require_fs_tools().fs_list(path)

    @m.tool()
    async def fs_glob(pattern: str) -> dict:
        return svc.require_fs_tools().fs_glob(pattern)

    @m.tool()
    async def fs_read(path: str, offset: int | None = None,
                      limit: int | None = None) -> dict:
        return svc.require_fs_tools().fs_read(path, offset=offset, limit=limit)

    @m.tool()
    async def fs_create(path: str, content: str,
                        resource_id: str | None = None,
                        expected_base_sha: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_create(path, content, resource_id=resource_id,
                                                expected_base_sha=expected_base_sha,
                                                idempotency_key=idempotency_key)

    @m.tool()
    async def fs_write(path: str, content: str,
                       resource_id: str | None = None,
                       expected_base_sha: str | None = None,
                       expected_resource_revision: str | None = None,
                       idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_write(path, content, resource_id=resource_id,
                                               expected_base_sha=expected_base_sha,
                                               expected_resource_revision=expected_resource_revision,
                                               idempotency_key=idempotency_key)

    @m.tool()
    async def fs_edit(path: str, old_string: str, new_string: str,
                      resource_id: str | None = None,
                      replace_all: bool = False,
                      expected_base_sha: str | None = None,
                      expected_resource_revision: str | None = None,
                      idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_edit(path, old_string, new_string,
                                              resource_id=resource_id,
                                              replace_all=replace_all,
                                              expected_base_sha=expected_base_sha,
                                              expected_resource_revision=expected_resource_revision,
                                              idempotency_key=idempotency_key)

    @m.tool()
    async def fs_copy(source: str, dest: str,
                      resource_id: str | None = None,
                      expected_base_sha: str | None = None,
                      idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_copy(source, dest, resource_id=resource_id,
                                              expected_base_sha=expected_base_sha,
                                              idempotency_key=idempotency_key)

    @m.tool()
    async def fs_rename(source: str, dest: str,
                        resource_id: str | None = None,
                        expected_base_sha: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_rename(source, dest, resource_id=resource_id,
                                                expected_base_sha=expected_base_sha,
                                                idempotency_key=idempotency_key)

    @m.tool()
    async def fs_delete(path: str,
                        resource_id: str | None = None,
                        expected_base_sha: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_delete(path, resource_id=resource_id,
                                                expected_base_sha=expected_base_sha,
                                                idempotency_key=idempotency_key)

    @m.tool()
    async def fs_batch(operations: list[dict],
                       expected_base_commit: str | None = None,
                       idempotency_key: str | None = None) -> dict:
        return svc.require_fs_tools().fs_batch(operations, expected_base_commit=expected_base_commit,
                                               idempotency_key=idempotency_key)

    return {
        "wiki_search": wiki_search,
        "wiki_query": wiki_query,
        "wiki_report_gap": wiki_report_gap,
        "wiki_ingest_plan": wiki_ingest_plan,
        "wiki_ingest_apply": wiki_ingest_apply,
        "wiki_list_docs": wiki_list_docs,
        "wiki_lint_mechanical": wiki_lint_mechanical,
        "fs_capabilities": fs_capabilities,
        "fs_resolve": fs_resolve,
        "fs_stat": fs_stat,
        "fs_list": fs_list,
        "fs_glob": fs_glob,
        "fs_read": fs_read,
        "fs_create": fs_create,
        "fs_write": fs_write,
        "fs_edit": fs_edit,
        "fs_copy": fs_copy,
        "fs_rename": fs_rename,
        "fs_delete": fs_delete,
        "fs_batch": fs_batch,
    }


# ── 单租户兼容面（模块级 mcp + configure，行为与历史一致）──────────────

mcp = FastMCP("katana-wiki-mcp", instructions=_INSTRUCTIONS)

_scope: str | None = None
_wiki_root: str | None = None
_kernel: GovernedKernel | None = None
_store: WikiStore | None = None
_fs_tools: FSTools | None = None


def configure(wiki_root: str, kb_root: str) -> None:
    global _scope, _wiki_root, _kernel, _store, _fs_tools
    svc = WikiService(wiki_root, kb_root)
    _scope = svc.scope
    _wiki_root = wiki_root
    _kernel = svc.kernel
    _store = svc.store
    _fs_tools = svc.fs_tools


def _is_wiki_path(path: str) -> bool:
    """Legacy module-level shim (uses configure() 设置的 _wiki_root)——测试兼容面。"""
    if not _wiki_root or not path:
        return False
    candidate = os.path.normpath(os.path.join(_wiki_root, path))
    root = os.path.normpath(_wiki_root)
    if not (candidate == root or candidate.startswith(root + os.sep)):
        return False
    return os.path.isfile(candidate)


def _do_search(query: str, top_k: int, scope: str | None) -> list[dict]:
    """Legacy module-level shim：显式传 scope 的单租户检索——测试兼容面。"""
    fetch_k = top_k if scope else max(top_k * 4, 20)
    resp = vault_search.search(query, top_k=fetch_k, dir=scope)
    out: list[dict] = []
    for r in resp.results:
        if not scope and not _is_wiki_path(r.path):
            continue
        out.append(
            {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
        )
        if len(out) >= top_k:
            break
    return out


class _DefaultServiceProxy:
    """模块级兼容面：svc 属性直接解析到 legacy 模块 globals。

    历史测试会绕过 configure() 直接注入 `_fs_tools`/`_kernel` 等 globals，
    因此这里必须逐属性读模块 globals，而不是缓存任何 service 实例。
    """

    @property
    def scope(self):
        return _scope

    @property
    def wiki_root(self):
        return _wiki_root

    @property
    def store(self):
        return _store

    @property
    def fs_tools(self):
        return _fs_tools

    def require_store(self) -> WikiStore:
        if _store is None:
            raise RuntimeError("wiki store not initialized; call configure() first")
        return _store

    def require_fs_tools(self) -> FSTools:
        if _fs_tools is None:
            raise RuntimeError("wiki fs_tools not initialized; call configure() first")
        return _fs_tools

    def is_wiki_path(self, path: str) -> bool:
        return _is_wiki_path(path)

    def do_search(self, query: str, top_k: int) -> list[dict]:
        return _do_search(query, top_k, _scope)

    def scoped_search(self, query: str, *, top_k: int = 10, dir: str | None = None, **kwargs):
        fetch_k = top_k if dir else max(top_k * 4, 20)
        resp = vault_search.search(query, top_k=fetch_k, dir=dir, **kwargs)
        if not dir:
            resp.results = [r for r in resp.results if _is_wiki_path(r.path)]
        del resp.results[top_k:]
        return resp

    def governed_append_log(self, wiki_root: str, line: str) -> None:
        if _store is not None:
            _store.append_gap_log(line)
        else:
            _pages.append_log(wiki_root, line)


_module_tools = register_tools(mcp, _DefaultServiceProxy())
wiki_search = _module_tools["wiki_search"]
wiki_query = _module_tools["wiki_query"]
wiki_report_gap = _module_tools["wiki_report_gap"]
wiki_ingest_plan = _module_tools["wiki_ingest_plan"]
wiki_ingest_apply = _module_tools["wiki_ingest_apply"]
wiki_list_docs = _module_tools["wiki_list_docs"]
wiki_lint_mechanical = _module_tools["wiki_lint_mechanical"]
fs_capabilities = _module_tools["fs_capabilities"]
fs_resolve = _module_tools["fs_resolve"]
fs_stat = _module_tools["fs_stat"]
fs_list = _module_tools["fs_list"]
fs_glob = _module_tools["fs_glob"]
fs_read = _module_tools["fs_read"]
fs_create = _module_tools["fs_create"]
fs_write = _module_tools["fs_write"]
fs_edit = _module_tools["fs_edit"]
fs_copy = _module_tools["fs_copy"]
fs_rename = _module_tools["fs_rename"]
fs_delete = _module_tools["fs_delete"]
fs_batch = _module_tools["fs_batch"]


# ── 多租户挂载 ────────────────────────────────────────────────────────

_TENANT_NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def load_tenant_map(path: str | os.PathLike) -> dict[str, str]:
    """读取 {tenant: wiki_root} JSON 映射文件并校验。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"tenant map {path} must be a non-empty JSON object")
    for tenant, root in data.items():
        if not isinstance(root, str) or not root:
            raise ValueError(f"tenant {tenant!r}: root must be a non-empty string")
        if not _TENANT_NAME_OK.match(tenant):
            raise ValueError(f"invalid tenant name {tenant!r}")
    return data


def build_tenant_server(tenant: str, wiki_root: str, kb_root: str) -> FastMCP:
    m = FastMCP(f"katana-wiki-mcp[{tenant}]", instructions=_INSTRUCTIONS)
    register_tools(m, WikiService(wiki_root, kb_root))
    return m


def build_app(tenant_map: dict[str, str], kb_root: str,
              default_tenant: str | None = None) -> Starlette:
    """多租户 app：每个租户挂 ``/t/<tenant>/mcp``；default_tenant 兼容挂 ``/mcp``。"""
    sub_apps: list = []
    mounts: list = []
    for tenant, root in sorted(tenant_map.items()):
        sub = build_tenant_server(tenant, root, kb_root).http_app(path="/mcp")
        sub_apps.append(sub)
        mounts.append(Mount(f"/t/{tenant}", app=sub))
    if default_tenant is not None:
        if default_tenant not in tenant_map:
            raise ValueError(f"default tenant {default_tenant!r} not in tenant map")
        legacy = build_tenant_server(
            default_tenant, tenant_map[default_tenant], kb_root
        ).http_app(path="/mcp")
        sub_apps.append(legacy)
        mounts.append(Mount("", app=legacy))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            for sub in sub_apps:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    return Starlette(routes=mounts, lifespan=lifespan)


def build_remote_app(
    wiki_root: str,
    kb_root: str,
    credential_registry,
    *,
    rate_limiter=None,
    readiness_service=None,
    audit_logger=None,
    tenant_resolver=None,
    tenant_map: dict[str, str] | None = None,
    default_tenant: str | None = None,
):
    """Build the wiki app with remote auth middleware applied.

    有 tenant_map 时包多租户 app；否则保持单租户历史行为。
    """
    from katana_remote import create_remote_app, RateLimiter, ReadinessService, AuditLogger

    if tenant_map is not None:
        inner = build_app(tenant_map, kb_root, default_tenant)
    else:
        configure(wiki_root, kb_root)
        inner = mcp.http_app()
    rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    readiness_service = readiness_service if readiness_service is not None else ReadinessService()
    audit_logger = audit_logger if audit_logger is not None else AuditLogger()

    return create_remote_app(
        inner,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain="wiki",
    )


def main() -> None:
    wiki_root = config.resolve("wiki_root", default=".", env_var="KATANA_WIKI_ROOT")
    kb = config.kb_root()
    host = os.environ.get("KATANA_WIKI_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WIKI_MCP_PORT", "5601"))
    creds = os.environ.get("KATANA_REMOTE_CREDENTIALS")
    tenants_file = os.environ.get("KATANA_WIKI_TENANTS")
    tenant_map = load_tenant_map(tenants_file) if tenants_file else None
    default_tenant = os.environ.get("KATANA_WIKI_DEFAULT_TENANT", "uther")
    if tenant_map is not None and default_tenant not in tenant_map:
        default_tenant = None

    if creds:
        import uvicorn

        from katana_remote.credstore import load_registry
        from katana_remote.runtime import audit_logger_from_env
        app = build_remote_app(wiki_root, kb, load_registry(creds),
                               audit_logger=audit_logger_from_env(),
                               tenant_map=tenant_map,
                               default_tenant=default_tenant)
        uvicorn.run(app, host=host, port=port)
        return
    if tenant_map is not None:
        import uvicorn

        uvicorn.run(build_app(tenant_map, kb, default_tenant), host=host, port=port)
        return
    configure(wiki_root, kb)
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
