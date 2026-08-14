#!/usr/bin/env python3
"""锚点校验：quote 是否真的出现在 anchor 指的位置。纯确定性，无模型。

v3 (2026-08-05 N2b): 以**生产者的实际格式**为准，而不是以校验器自己脑子里的格式为准。

生产者 `composeAnchor(source, locator, revision, range)` 组装为
    <source>://<locator>@<revision>#<range>
真实实例（2026-08-05 `research:v1-tick-reclaim.evidence`，V1 真跑）：
    code://src/tick.ts@a592276892f5e93a5e37d800a52dd48436639c0b#L202-L211
其中 locator 是**仓内相对路径**（不含仓名），revision 是 commit sha。

因为 locator 不含仓名，单看锚点无法确定是哪个仓 ⇒ 仓根必须由调用方以
`--repo-root` 显式提供；缺失时**响亮失败**，绝不猜测、绝不遍历 CODE_ROOTS 撞运气。

v4 (2026-08-14 E3): 认第四种 scheme `web://`，并把「本期不支持的 scheme」显式单列。

`web://` 的实际形态（派发方 2026-08-14 真机取证，E1c 交付形态）：
    web://<uri>@<digest>#<range>
    web://http://127.0.0.1:50287/e1-material2.png@9bee527f…（64 位）…#L3:1-43
三个部件的事实：
  - <uri>    ：**未做 url-encode 的原始 URI**，本身含 `://` ⇒ URI 段必须贪婪匹配，
               ⛔ 不能用 `[^@]+`（真实 URI 可含 userinfo 的 `@`）、⛔ 不能用 `[^:]+`。
  - <digest> ：ingest 对取回字节权威计算的**完整 64 位** sha256，⛔ 不是 git 的 7–40 位 sha。
  - <range>  ：`L<a>` / `L<a>-L<b>` / `L<a>:<c1>-<c2>` 三种都可能出现（E1c 只归一 `L` 前缀，
               不归一内部语法），三种都要认；认不出的 range 语法 ⇒ **响亮失败**。
⇒ 解析以**结尾的定长 64 hex digest** 为切分依据（见 WEB_URI_RE）。

⭐ `web://` 的核验数据源是 **bus 上不可变的 transcript，不是线上网页**：
按 `<digest>` 在 `research:content` 这条全局 channel 上找到 `doc_kind="transcript"` 的
`research.doc.v2`，在其 `body` 里比对逐字引文 ⇒ 完全离线、确定性、可重放。
⛔ 本工具**绝不联网重新抓取 `<uri>`**（网页会改版，那份 transcript 不会——这正是
`web://` 的核验硬度能与 `code://` 同级的根据）。channel 与 bus 地址与 `--repo-root` 同纪律：
必须由调用方显式提供，缺失即**响亮失败**，绝不猜、绝不撞运气。

五类锚点**显式**分类（分别计数，总和必须 === 输入条数，任何一类不得静默丢弃）：
  1) 现行格式  code://<path>@<sha>#L<a>[-L<b>]   → 解析并**真正校验**（取该 revision 的文件，比对 quote）
  2) web 格式  web://<uri>@<64hex>#<range>       → 解析并**真正校验**（取 bus 上的 transcript，比对 quote）
  3) 旧格式    裸 path:line                       → 显式标为「旧格式，不可校验 revision」，**独立计数**
  4) 本期不支持的 scheme（wiki:// / feishu:// …） → 显式标为 `unsupported_scheme`，**独立计数**，
     **不进命中率分母**、**本身不导致非零退出**。它们的 digest 不在 `research:content` 上
     （不走 ingest），离线判据对其不成立 ⇒ 显式披露，⛔ 不是「取不到就悄悄不计」的暗箱。
     ⛔ 必须与 `unparseable`（**根本解析不了**）区分开：`wiki://x@y#z` 是「格式合法但本期不支持」。
  5) 其它                                        → 显式标为「不可解析」, **独立计数**

fetcher 自检：取回内容必须**非空且形态合理**（行数 > 0 且能定位到给定行段/字符段），
否则**响亮失败**——⛔ 不得当成「引文不匹配」。

⛔ 只读：本工具不向任何 channel 写入。
"""
import argparse, json, os, re, subprocess, sys
from collections import Counter

# ── 现行格式（生产者的实际格式）：code://<path>@<sha>#L<a>[-L<b>]
#    path 为仓内相对路径（不含仓名），sha 为 revision。
CURRENT_URI_RE = re.compile(
    r'^code://([^@]+)@([0-9a-fA-F]{7,40})#L(\d+)(?:-L?(\d+))?$'
)
# ── web 格式：web://<uri>@<64位hex>#<range>
#    URI 段**贪婪**（原始 URI 未 url-encode，自身含 `://`，也可能含 `@`），
#    以定长 64 hex digest 作切分锚；range 原样带出，由 parse_range() 再判。
#    ⛔ 不得放宽 digest 宽度去兼容 code:// 的 {7,40}。
WEB_URI_RE = re.compile(r'^web://(.+)@([0-9a-fA-F]{64})#(.+)$')
# ── 本期不支持的 scheme（wiki:// / feishu:// …）：形态合法，但 digest 不在 research:content 上。
#    ⛔ 负向断言排除 code:// 与 web://：这两种前缀写坏了应落到「不可解析」，不是「不支持」。
UNSUPPORTED_SCHEME_RE = re.compile(
    r'^(?!code://)(?!web://)([A-Za-z][A-Za-z0-9+.\-]*)://(.+)$'
)
# 旧格式：裸 path:line / path:lo-hi / path:a-b,c-d（历史 131 条）
OLD_URI_RE = re.compile(r'^(?!code://)(.+):\d+(?:[-,]\d+)*$')

# ── range 三种语法（GT-2 实测：16/16 条为 L<a>:<c1>-<c2>，另两种由 E1c 语义保留）
RANGE_CHARS_RE = re.compile(r'^L(\d+):(\d+)-(\d+)$')   # L<a>:<c1>-<c2>
RANGE_LINES_RE = re.compile(r'^L(\d+)-L?(\d+)$')       # L<a>-L<b>
RANGE_LINE_RE = re.compile(r'^L(\d+)$')                # L<a>

DEFAULT_BUS_URL = "http://127.0.0.1:7490"
DEFAULT_BUS_TOKEN_FILE = "/data/agent-bus/tokens/line-deep-research.token"


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def bus_messages(bus_url, channel, token_file, limit=1000):
    """只读地取 bus 某 channel 的全集消息。⛔ 只 GET，不向任何 channel 写入。"""
    tok = open(token_file).read().strip()
    import urllib.request
    req = urllib.request.Request(
        f"{bus_url.rstrip('/')}/v1/channels/{channel}/messages?limit={limit}",
        headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req, timeout=30)).get("messages", [])


def load_entries(corpus, bus_url=None, token_file=None):
    """从导出文件或 live bus channel 读取条目，返回 (entries, discarded)。

    导出文件：JSON 数组，每条可为
      - bus 消息形态 {"payload": {"anchor":..., "quote":...}}，或
      - 直接 {"anchor":..., "quote":...}
    corpus 以 `bus:<channel>` 前缀时直连 bus 取全集。

    ⛔ 无声丢弃即失败：任何记录若无法提取（非 dict / payload 非 dict /
    缺 anchor 或 anchor 为空），计入 `discarded` 并原样保留在总数里，
    绝不静默丢掉——由调用方据此判定 exit 3。
    """
    quote_keys = ("quote", "text", "snippet")
    def extract(rec):
        if isinstance(rec, dict) and "payload" in rec and isinstance(rec["payload"], dict):
            pl = rec["payload"]
        elif isinstance(rec, dict):
            pl = rec
        else:
            return None
        anchor = pl.get("anchor")
        if not anchor:
            return None
        quote = next((pl[k] for k in quote_keys if pl.get(k)), "")
        return {"anchor": anchor, "quote": quote or ""}

    entries = []
    discarded = 0

    if corpus.startswith("bus:"):
        ch = corpus.split("bus:", 1)[1]
        ms = bus_messages(bus_url or DEFAULT_BUS_URL, ch,
                          token_file or DEFAULT_BUS_TOKEN_FILE)
        for m in ms:
            e = extract(m)
            if e is None:
                discarded += 1
            else:
                entries.append(e)
        return entries, discarded

    with open(corpus, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("messages", [data])
    if not isinstance(data, list):
        data = [data]
    for r in data:
        e = extract(r)
        if e is None:
            discarded += 1
        else:
            entries.append(e)
    return entries, discarded


def classify(anchor):
    """返回 (类别, 解析结果)。

    类别 ∈ {current, web, old, unsupported_scheme, unparseable}。
    ⛔ code:// 与旧格式的判定逐字不变（回归判据）：新增分支一律排在 code:// 之后、
       且以负向断言把 code:// / web:// 前缀排除在 unsupported_scheme 之外。
    """
    m = CURRENT_URI_RE.match(anchor)
    if m:
        return "current", {
            "path": m.group(1),
            "rev": m.group(2),
            "lo": int(m.group(3)),
            "hi": int(m.group(4)) if m.group(4) else int(m.group(3)),
        }
    m = WEB_URI_RE.match(anchor)
    if m:
        # range 原样带出：认不出的 range 语法要走**响亮失败**，
        # ⛔ 不能在这里降级成「不可解析」而把整条锚点吞掉。
        return "web", {"uri": m.group(1), "digest": m.group(2), "range": m.group(3)}
    m = UNSUPPORTED_SCHEME_RE.match(anchor)
    if m:
        return "unsupported_scheme", {"scheme": m.group(1)}
    if anchor.startswith("code://") or anchor.startswith("web://"):
        # 认得前缀但不合该 scheme 的格式 ⇒ 不可解析（⛔ 不得落进旧格式）
        return "unparseable", None
    m = OLD_URI_RE.match(anchor)
    if m:
        return "old", {"path": m.group(1)}
    return "unparseable", None


def parse_range(raw):
    """把 range 串解析为 {lo, hi, c1, c2}；认不出 → None（调用方**响亮失败**）。

    三种语法（GT-2）：L<a> / L<a>-L<b> / L<a>:<c1>-<c2>。
    c1/c2 为**该行内**的 1-based 闭区间字符位（实测样本 L7:12-308 与 L7:314-542 同行
    且不单调，故非全文偏移）。
    """
    m = RANGE_CHARS_RE.match(raw)
    if m:
        ln = int(m.group(1))
        return {"lo": ln, "hi": ln, "c1": int(m.group(2)), "c2": int(m.group(3))}
    m = RANGE_LINES_RE.match(raw)
    if m:
        return {"lo": int(m.group(1)), "hi": int(m.group(2)), "c1": None, "c2": None}
    m = RANGE_LINE_RE.match(raw)
    if m:
        ln = int(m.group(1))
        return {"lo": ln, "hi": ln, "c1": None, "c2": None}
    return None


class ContentStore:
    """`research:content` 上 `doc_kind="transcript"` 的 research.doc.v2，按 digest 索引。

    ⭐ 这是 `web://` 的**唯一**取材口：⛔ 绝不联网抓取 anchor 里的 `<uri>`。
    取材源与 bus 地址必须由调用方显式提供（`--content-source` / `--bus-url`），
    缺失即**响亮失败**——与 `--repo-root` 同纪律，绝不猜、绝不撞运气。
    """

    def __init__(self, source=None, bus_url=None, token_file=None):
        self.source = source
        self.bus_url = bus_url
        self.token_file = token_file or DEFAULT_BUS_TOKEN_FILE
        self._by_digest = None
        self._load_err = None

    @staticmethod
    def _unwrap(rec):
        """bus 消息 {"payload": {...}} / 直接 doc / {"payload": {"doc": {...}}} 都认。"""
        if not isinstance(rec, dict):
            return None
        d = rec
        if isinstance(d.get("payload"), dict):
            d = d["payload"]
        if isinstance(d.get("doc"), dict) and "digest" in d["doc"]:
            d = d["doc"]
        return d

    def _load(self):
        if self._by_digest is not None or self._load_err is not None:
            return
        if not self.source:
            self._load_err = ("缺 --content-source（web:// 的取材源 research:content "
                              "必须外部提供，绝不猜测；⛔ 不得改为联网抓取 <uri>）")
            return
        try:
            if self.source.startswith("bus:"):
                ch = self.source.split("bus:", 1)[1]
                if not self.bus_url:
                    self._load_err = "缺 --bus-url（bus 地址必须外部提供，绝不猜测）"
                    return
                recs = bus_messages(self.bus_url, ch, self.token_file)
            else:
                with open(self.source, encoding="utf-8") as f:
                    recs = json.load(f)
                if isinstance(recs, dict):
                    recs = recs.get("messages", [recs])
                if not isinstance(recs, list):
                    recs = [recs]
        except Exception as ex:  # noqa: BLE001
            self._load_err = (f"取 research:content 失败: {type(ex).__name__}: {ex}"
                              f"（source={self.source}）")
            return
        idx = {}
        for r in recs:
            d = self._unwrap(r)
            if not isinstance(d, dict):
                continue
            if d.get("doc_kind") != "transcript":
                continue
            dg = d.get("digest")
            if isinstance(dg, str) and dg and dg not in idx:
                idx[dg] = d
        self._by_digest = idx

    def get_transcript(self, digest):
        """按 digest 取 transcript doc；取不到/为空 → (None, 错误信息) ⇒ **响亮失败**。"""
        self._load()
        if self._load_err:
            return None, self._load_err
        doc = self._by_digest.get(digest)
        if doc is None:
            return None, (f"fetcher 取不到 transcript：research:content 上无 digest={digest} "
                          f"的 doc_kind=transcript（⛔ 不得当成引文不匹配）")
        body = doc.get("body")
        if not isinstance(body, str) or not body.strip():
            return None, (f"fetcher 取回空 transcript: digest={digest}"
                          f"（形态不合理，⛔ 不得当成引文不匹配）")
        return doc, None


def locate_web_window(body, rng):
    """在 transcript body 上按 range 定位窗口；定位不到 → (None, 错误信息) ⇒ **响亮失败**。"""
    lines = body.splitlines()
    lo, hi = rng["lo"], rng["hi"]
    if lo < 1 or hi < lo or hi > len(lines):
        return None, (f"transcript 行数 {len(lines)} 无法定位到 L{lo}-L{hi}（形态不合理）")
    if rng["c1"] is None:
        return "\n".join(lines[lo - 1:hi]), None
    line = lines[lo - 1]
    c1, c2 = rng["c1"], rng["c2"]
    if c1 < 1 or c2 < c1 or c2 > len(line):
        return None, (f"transcript 第 {lo} 行长度 {len(line)} 无法定位到字符段 "
                      f"{c1}-{c2}（形态不合理）")
    return line[c1 - 1:c2], None


def fetch(repo_root, rev, relpath):
    """取回给定 revision 的文件内容。自检：非空且形态合理。

    若取回失败 / 空 / 形态不合理 → 返回 (None, 错误信息)，由调用方**响亮失败**。
    """
    if not repo_root:
        return None, "缺 --repo-root（仓根必须外部提供，绝不猜测）"
    try:
        r = subprocess.run(
            ["git", "-C", repo_root, "show", f"{rev}:{relpath}"],
            capture_output=True, text=True, timeout=20, check=True)
    except subprocess.CalledProcessError as e:
        return None, f"fetcher 取不到该 revision: git show {rev}:{relpath} rc={e.returncode}"
    except Exception as e:  # noqa: BLE001
        return None, f"fetcher 执行失败: {type(e).__name__}: {e}"
    content = r.stdout
    if not content or not content.strip():
        return None, f"fetcher 取回空内容: git show {rev}:{relpath}（rc=0 但无输出）"
    if len(content.splitlines()) < 1:
        return None, f"fetcher 取回内容形态不合理（无有效行）: {rev}:{relpath}"
    return content, None


def main():
    ap = argparse.ArgumentParser(description="anchor-check v4 (E3: +web://)")
    ap.add_argument("--corpus", required=True,
                    help="导出 JSON 文件路径，或 bus:<channel> 直连")
    ap.add_argument("--repo-root", default=None,
                    help="仓根（现行格式 locator 为仓内相对路径，必须外部提供）")
    ap.add_argument("--content-source", default=None,
                    help="web:// 的取材源：research:content 的导出 JSON 路径，"
                         "或 bus:<channel>（需配 --bus-url）。⛔ 缺失时 web:// 一律响亮失败")
    ap.add_argument("--bus-url", default=None,
                    help=f"bus 基址（--content-source bus: 时必填，绝不猜；"
                         f"--corpus bus: 未指定时沿用 {DEFAULT_BUS_URL}）")
    ap.add_argument("--bus-token-file", default=None,
                    help=f"bus token 文件（默认 {DEFAULT_BUS_TOKEN_FILE}）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    entries, discarded = load_entries(args.corpus, args.bus_url, args.bus_token_file)
    total = len(entries) + discarded

    content_store = ContentStore(args.content_source, args.bus_url, args.bus_token_file)

    cur_parsed = 0
    cur_hit = 0
    cur_fail = 0
    web_parsed = 0
    web_hit = 0
    web_fail = 0
    old_count = 0
    unsupported = 0
    unparseable = 0
    loud_failures = []
    details = []

    for e in entries:
        anchor = e["anchor"]
        kind, parsed = classify(anchor)
        if kind == "old":
            old_count += 1
            details.append(("old", anchor, "旧格式，不可校验 revision"))
            continue
        if kind == "unsupported_scheme":
            # 本期不核验（digest 不在 research:content 上），但**显式披露**：
            # 不进命中率分母、⛔ 本身不导致非零退出。
            unsupported += 1
            details.append(("unsupported_scheme", anchor,
                            f"scheme={parsed['scheme']}://本期不支持（显式披露，不进分母）"))
            continue
        if kind == "unparseable":
            unparseable += 1
            details.append(("unparseable", anchor, "不可解析"))
            continue
        if kind == "web":
            web_parsed += 1
            rng = parse_range(parsed["range"])
            if rng is None:
                loud_failures.append(
                    (anchor, f"range 语法认不出: #{parsed['range']}"
                             f"（⛔ 不得静默命中、⛔ 不得静默未命中）"))
                continue
            doc, err = content_store.get_transcript(parsed["digest"])
            if err:
                loud_failures.append((anchor, err))
                continue
            window, err = locate_web_window(doc["body"], rng)
            if err:
                loud_failures.append((anchor, err))
                continue
            q = norm(e["quote"])
            if q and q in norm(window):
                web_hit += 1
                details.append(("web-hit", anchor, "命中"))
            else:
                web_fail += 1
                details.append(("web-mismatch", anchor,
                                "引文不在该 transcript 的指定位置（或为空白引文）"))
            continue
        # current
        cur_parsed += 1
        content, err = fetch(args.repo_root, parsed["rev"], parsed["path"])
        if err:
            loud_failures.append((anchor, err))
            continue
        lines = content.splitlines()
        lo, hi = parsed["lo"], parsed["hi"]
        if lo < 1 or hi > len(lines):
            loud_failures.append(
                (anchor, f"取回内容行数 {len(lines)} 无法定位到 L{lo}-L{hi}（形态不合理）"))
            continue
        window = norm("\n".join(lines[lo - 1:hi]))
        q = norm(e["quote"])
        if q and q in window:
            cur_hit += 1
            details.append(("current-hit", anchor, "命中"))
        else:
            cur_fail += 1
            details.append(("current-mismatch", anchor,
                            "引文不在指定行段（或为空白引文）"))

    # 五类计数 + 被丢弃记录必须 === 输入总数；任何丢弃都意味着「无声丢弃」→ sums_ok=False
    sums_ok = ((cur_parsed + web_parsed + old_count + unsupported + unparseable + discarded)
               == total and discarded == 0)

    if args.json:
        out = {
            "total": total,
            "current_parsed": cur_parsed,
            "current_verified_hit": cur_hit,
            "current_failed": cur_fail,
            "web_parsed": web_parsed,
            "web_verified_hit": web_hit,
            "web_failed": web_fail,
            "old_format": old_count,
            "unsupported_scheme": unsupported,
            "unparseable": unparseable,
            "discarded": discarded,
            "sums_ok": sums_ok,
            "loud_failures": [{"anchor": a, "error": e} for a, e in loud_failures],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"输入条目总数: {total}")
        print(f"  现行格式已解析: {cur_parsed}   （真正校验命中 {cur_hit} / 未命中 {cur_fail}）")
        print(f"  web 格式已解析: {web_parsed}   （真正校验命中 {web_hit} / 未命中 {web_fail}；"
              f"取材为 research:content 上的 transcript，⛔ 未联网抓取 <uri>）")
        print(f"  旧格式 path:line: {old_count}   （不可校验 revision，独立计数）")
        print(f"  本期不支持的 scheme: {unsupported}   "
              f"（显式披露；不进命中率分母，⛔ 不是「取不到就悄悄不计」）")
        print(f"  不可解析: {unparseable}   （独立计数）")
        print(f"  被丢弃（缺 anchor / 无法提取）: {discarded}   （⛔ 无声丢弃即失败）")
        print(f"  计数之和 === 输入条数: {sums_ok}")
        if unsupported:
            print("\n【本期不支持的 scheme（显式披露，不导致非零退出）】")
            for kind, a, extra in details:
                if kind == "unsupported_scheme":
                    print(f"  {a}  {extra}")
        if loud_failures:
            print("\n【响亮失败】")
            for a, e in loud_failures:
                print(f"  {a}\n    -> {e}")
        if not sums_ok or cur_fail or web_fail:
            print("\n【未命中明细】")
            for kind, a, extra in details:
                if kind in ("current-mismatch", "web-mismatch", "old",
                            "unsupported_scheme", "unparseable"):
                    print(f"  {kind:<18} {a}  {extra}")

    # 退出码（既有含义不变，web:// 与 code:// 同级）：
    #   0  → 无响亮失败 且 无引文未命中
    #   1  → 有现行格式 / web 格式引文未命中（校验失败）
    #   2  → 有响亮失败（缺 repo-root / 缺 content-source / fetcher 取不到 / 形态不合理）
    #   3  → 各类计数之和与输入条数不符（静默丢弃）
    # ⛔ unsupported_scheme 本身**不得**导致非零退出：它是显式披露，不是失败。
    if loud_failures:
        sys.exit(2)
    if not sums_ok:
        sys.exit(3)
    if cur_fail or web_fail:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()