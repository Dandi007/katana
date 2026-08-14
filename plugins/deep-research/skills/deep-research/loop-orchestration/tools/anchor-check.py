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

v4 (2026-08-14 E3): 认第四种 scheme `web://`（内容锚点），并把「其它 scheme」
从「不可解析」里拆出来**单列**。

    web://<uri>@<digest>#<range>
真实实例（2026-08-14 真机全链路跑出，证据 channel 16 条之一）：
    web://http://127.0.0.1:50287/e1-material2.png@9bee527fe5f6e5ddef93194f3ede333b964ff9b50c8db013aef1dc6659fe1675#L3:1-43
三个部件的实测事实（⛔ 以实产为准，不是设计稿）：
  <uri>    —— **未做 url-encode 的原始 URI**，自身含 `://` ⇒ URI 段必须**贪婪**匹配，
              ⛔ 不能写成 `[^@]+` 或非贪婪（那会在 URI 自己的 `//` 上断掉）。
              切分依据是**结尾定长的 hex digest**。
  <digest> —— **定长 64 位** sha256（ingest 对取回字节权威计算），
              ⛔ 不得沿用 `code://` 的 `{7,40}` 宽度去兼容。
  <range>  —— 与 `code://` **不是同一套语法**；E1c 只归一 `L` 前缀、不归一内部语法，
              故 `L<a>` / `L<a>-L<b>` / `L<a>:<c1>-<c2>` 三种都要认。

五类锚点**显式**分类（分别计数，总和必须 === 输入条数，任何一类不得静默丢弃）：
  1) 现行代码格式 code://<path>@<sha>#L<a>[-L<b>]  → 解析并**真正校验**（取该 revision 的文件，比对 quote）
  2) 内容格式     web://<uri>@<64位hex>#<range>    → 解析并**真正校验**（取 bus 上的 transcript，比对 quote）
  3) 旧格式       裸 path:line                     → 显式标为「旧格式，不可校验 revision」，**独立计数**
  4) 其它 scheme  wiki:// / feishu:// …            → 「格式合法但本期不核验」，**独立计数**，
                                                     ⛔ 不进命中率分母、⛔ 本身不导致非零退出
  5) 其它                                          → 显式标为「不可解析」，**独立计数**

⚠️ 第 4 类与第 5 类**必须区分**：`wiki://x@y#z` 是「认得出形态、本期不支持这个信源」，
   不是「根本解析不了」。合并两者就把「本期已知不核验」伪装成「输入是垃圾」。

⛔ `web://` 的核验对象**不是线上网页**：
   是 `research:content` 这条全局 channel 上那份不可变的 `research.doc.v2`
   （`doc_kind="transcript"`）。按 `<digest>` 找到该 doc，在其 `body` 里比对逐字引文
   ⇒ 完全离线、确定性、可重放（网页会改版，那份 transcript 不会）。
   **全程不对 `<uri>` 发起任何网络请求。**
   channel 与 bus 地址必须由调用方显式提供（`--content-channel` + `--bus-url`，
   或离线导出 `--content-export`）；缺失时**响亮失败**——与 `--repo-root` 同纪律，
   绝不猜、绝不撞运气。

fetcher 自检（`code://` 与 `web://` 同构）：取回内容必须**非空且形态合理**
（能定位到给定 range），否则**响亮失败**——⛔ 不得当成「引文不匹配」。

⛔ 只读：本工具不向任何 channel 写入。
"""
import argparse, json, os, re, subprocess, sys
from collections import Counter

# ── 现行格式（生产者的实际格式）：code://<path>@<sha>#L<a>[-L<b>]
#    path 为仓内相对路径（不含仓名），sha 为 revision。
CURRENT_URI_RE = re.compile(
    r'^code://([^@]+)@([0-9a-fA-F]{7,40})#L(\d+)(?:-L?(\d+))?$'
)
# 内容格式：web://<uri>@<64位hex sha256>#<range>
# ⛔ URI 段**贪婪**（`(.+)`）：真实 uri 未做 url-encode、自身含 `://` 与可能的 `@`；
#    以结尾的**定长 64 hex** digest 作切分依据。写成 `[^@]+` 或非贪婪即在 URI 内部断掉。
WEB_URI_RE = re.compile(r'^web://(.+)@([0-9a-fA-F]{64})#(.+)$')
# 旧格式：裸 path:line / path:lo-hi / path:a-b,c-d（历史 131 条）
OLD_URI_RE = re.compile(r'^(?!code://)(.+):\d+(?:[-,]\d+)*$')
# 其它 scheme：composeAnchor 形态（<scheme>://<locator>@<revision>[#<range>]）但 scheme 本期不核验。
# ⛔ 在 OLD_URI_RE **之后**求值：旧格式判定必须逐字不变（判据 8）。
OTHER_SCHEME_RE = re.compile(
    r'^(?!code://)(?!web://)([A-Za-z][A-Za-z0-9+.\-]*)://(.+)@([^@#]+)(?:#(.+))?$'
)

# range 三种语法（GT-2 实测：16/16 条为 L<a>:<c1>-<c2>；另两种由 E1c 的「只归一 L 前缀」推出必须兼容）
RANGE_LINES_RE = re.compile(r'^L(\d+)(?:-L?(\d+))?$')          # L<a> / L<a>-L<b> / L<a>-<b>
RANGE_CHARS_RE = re.compile(r'^L(\d+):(\d+)-(\d+)$')           # L<a>:<c1>-<c2>

# bus 默认值仅用于 `--corpus bus:<channel>`（v3 既有行为，逐字保留）。
# ⛔ `web://` 的 transcript 源**没有**默认值：缺失即响亮失败。
DEFAULT_BUS_URL = "http://127.0.0.1:7490"
DEFAULT_BUS_TOKEN_FILE = "/data/agent-bus/tokens/line-deep-research.token"
DOC_MESSAGE_KIND = "research.doc.v2"
TRANSCRIPT_DOC_KIND = "transcript"


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def bus_get_messages(bus_url, channel, token_file, limit=100, after_seq=None):
    """读一页 channel 消息。只读，绝不写入。"""
    import urllib.request
    tok = open(token_file).read().strip()
    params = f"limit={limit}"
    if after_seq is not None:
        params += f"&after_seq={after_seq}"
    req = urllib.request.Request(
        f"{bus_url}/v1/channels/{channel}/messages?{params}",
        headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req, timeout=30)).get("messages", [])


def load_entries(corpus, bus_url=DEFAULT_BUS_URL, token_file=DEFAULT_BUS_TOKEN_FILE):
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
        ms = bus_get_messages(bus_url, ch, token_file, limit=1000)
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
    求值顺序有意义：
      web 必须在 old 之前——`web://…#L3:1-43` 结尾是 `:1-43`，会被旧格式正则吃掉；
      unsupported_scheme 必须在 old 之后——旧格式判定要逐字不变（判据 8）。
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
        return "web", {
            "uri": m.group(1),
            "digest": m.group(2),
            "range": m.group(3),
        }
    m = OLD_URI_RE.match(anchor)
    if m:
        return "old", {"path": m.group(1)}
    m = OTHER_SCHEME_RE.match(anchor)
    if m:
        return "unsupported_scheme", {"scheme": m.group(1)}
    return "unparseable", None


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


class TranscriptStore:
    """`web://` 的取材源：`research:content` 上不可变的 `research.doc.v2`(transcript)。

    ⛔ 只按 `<digest>` 在这条 channel 上查；**绝不**按 `<uri>` 联网抓取网页——
       核验对象是那份不可变的 transcript，不是会改版的线上页面。
    源必须由调用方显式提供（`--content-export`，或 `--content-channel` + `--bus-url`），
    缺失即响亮失败——与 `--repo-root` 同纪律。
    索引惰性构建：没有 `web://` 锚点时一个请求都不发。
    """

    MISSING_SOURCE = ("缺 transcript 源（--content-channel + --bus-url，或 --content-export）"
                      "：web:// 的核验源必须外部提供，绝不猜、⛔ 绝不联网抓 <uri>")

    def __init__(self, export_path=None, channel=None, bus_url=None,
                 token_file=DEFAULT_BUS_TOKEN_FILE):
        self.export_path = export_path
        self.channel = channel
        self.bus_url = bus_url
        self.token_file = token_file
        self._index = None
        self._error = None

    def configured(self):
        return bool(self.export_path) or bool(self.channel and self.bus_url)

    @staticmethod
    def index_messages(messages):
        """纯函数：按 digest 建 transcript 索引（同 digest 后者覆盖前者）。"""
        index = {}
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if isinstance(msg.get("payload"), dict):
                if msg.get("kind") not in (None, DOC_MESSAGE_KIND):
                    continue
                doc = msg["payload"]
            else:
                doc = msg
            if doc.get("doc_kind") != TRANSCRIPT_DOC_KIND:
                continue
            digest = doc.get("digest")
            if not digest:
                continue
            index[digest] = doc
        return index

    def _load(self):
        if self._index is not None or self._error is not None:
            return
        try:
            if self.export_path:
                with open(self.export_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = data.get("messages", [data])
                if not isinstance(data, list):
                    data = [data]
                self._index = self.index_messages(data)
            else:
                # 分页扫描：`GET /v1/channels/<id>/messages` 默认只给最早一批，
                # 不带 after_seq 翻页就看不到后来的 doc。
                messages, after_seq = [], None
                while True:
                    page = bus_get_messages(self.bus_url, self.channel,
                                            self.token_file, after_seq=after_seq)
                    if not page:
                        break
                    messages.extend(page)
                    nxt = page[-1].get("channel_seq")
                    if nxt is None or (after_seq is not None and nxt <= after_seq):
                        break  # 无前进守卫：异常后端不得导致死循环
                    after_seq = nxt
                self._index = self.index_messages(messages)
        except Exception as e:  # noqa: BLE001
            src = self.export_path or f"{self.bus_url} {self.channel}"
            self._error = f"transcript 源取不到: {src} -> {type(e).__name__}: {e}"

    def body_for(self, digest):
        """按 digest 取 transcript 正文。返回 (body, 错误信息)；有错即**响亮失败**。"""
        if not self.configured():
            return None, self.MISSING_SOURCE
        self._load()
        if self._error:
            return None, self._error
        doc = self._index.get(digest)
        if doc is None:
            where = self.export_path or f"{self.channel}@{self.bus_url}"
            return None, (f"fetcher 取不到该 transcript: {where} 上没有 "
                          f"doc_kind=transcript 且 digest={digest} 的 doc"
                          "（⛔ 这是响亮失败，不是引文不匹配）")
        body = doc.get("body")
        if not body or not str(body).strip():
            return None, (f"fetcher 取回空 transcript: digest={digest}"
                          "（doc 在，但 body 为空 ⇒ 形态不合理）")
        return str(body), None


def locate_range(content, rng):
    """按 range 在正文里取出待比对窗口。返回 (窗口, 错误信息)。

    认三种语法（GT-2）：
      L<a>            —— 第 a 行整行
      L<a>-L<b> / L<a>-<b> —— 第 a..b 行
      L<a>:<c1>-<c2>  —— 第 a 行的第 c1..c2 个字符（1-based 闭区间）
    ⛔ 认不出的 range 语法 ⇒ 报错（响亮失败），绝不静默当成命中或未命中。
    ⛔ 定位不到（行不存在 / 起点越出行尾）⇒ 报错。终点越界按行尾截断——
       容忍生产者开/闭区间口径差异；截短的窗口只会让引文比对**不命中**，不会假命中。
    """
    lines = content.splitlines()
    m = RANGE_CHARS_RE.match(rng)
    if m:
        ln, c1, c2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if ln < 1 or ln > len(lines):
            return None, f"取回内容行数 {len(lines)} 无法定位到 {rng}（形态不合理）"
        line = lines[ln - 1]
        if c1 < 1 or c2 < c1:
            return None, f"range 字符区间不合理: {rng}"
        if c1 > len(line):
            return None, (f"第 {ln} 行长度 {len(line)} 无法定位到 {rng} 的起点"
                          f"（形态不合理）")
        window = line[c1 - 1:min(c2, len(line))]
        if not window.strip():
            return None, f"定位到的窗口为空: {rng}（形态不合理）"
        return window, None
    m = RANGE_LINES_RE.match(rng)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo < 1 or hi > len(lines) or hi < lo:
            return None, f"取回内容行数 {len(lines)} 无法定位到 {rng}（形态不合理）"
        return "\n".join(lines[lo - 1:hi]), None
    return None, (f"认不出的 range 语法: {rng}"
                  "（⛔ 不得静默当成命中或未命中）")


def main():
    ap = argparse.ArgumentParser(description="anchor-check v4 (E3: +web://)")
    ap.add_argument("--corpus", required=True,
                    help="导出 JSON 文件路径，或 bus:<channel> 直连")
    ap.add_argument("--repo-root", default=None,
                    help="仓根（code:// 的 locator 为仓内相对路径，必须外部提供）")
    ap.add_argument("--content-channel", default=None,
                    help="transcript 所在 channel（如 research:content）；"
                         "web:// 的核验源，必须与 --bus-url 同时给，⛔ 无默认值")
    ap.add_argument("--content-export", default=None,
                    help="transcript 的离线导出 JSON（research.doc.v2 消息数组），"
                         "与 --content-channel 二选一")
    ap.add_argument("--bus-url", default=None,
                    help=f"bus 地址（如 http://127.0.0.1:7495）；不给时 --corpus bus: 用 {DEFAULT_BUS_URL}")
    ap.add_argument("--bus-token-file", default=DEFAULT_BUS_TOKEN_FILE,
                    help="bus token 文件路径")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--classify-only", action="store_true",
                    help="只做分类与解析并输出 JSON（不取材、不联网），供判别性自检使用")
    args = ap.parse_args()

    if args.content_export and args.content_channel:
        ap.error("--content-export 与 --content-channel 互斥：transcript 源必须唯一且显式")
    if args.content_channel and not args.bus_url:
        ap.error("--content-channel 必须与 --bus-url 同时提供（bus 地址绝不猜测）")

    entries, discarded = load_entries(
        args.corpus,
        bus_url=args.bus_url or DEFAULT_BUS_URL,
        token_file=args.bus_token_file)
    total = len(entries) + discarded

    if args.classify_only:
        out = []
        for e in entries:
            kind, parsed = classify(e["anchor"])
            out.append({"anchor": e["anchor"], "kind": kind, "parsed": parsed})
        print(json.dumps({"total": total, "discarded": discarded, "entries": out},
                         ensure_ascii=False, indent=2))
        sys.exit(0)

    store = TranscriptStore(export_path=args.content_export,
                            channel=args.content_channel,
                            bus_url=args.bus_url,
                            token_file=args.bus_token_file)

    cur_parsed = 0
    cur_hit = 0
    cur_fail = 0
    web_parsed = 0
    web_hit = 0
    web_fail = 0
    old_count = 0
    unsupported_scheme = 0
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
            unsupported_scheme += 1
            details.append(("unsupported-scheme", anchor,
                            f"scheme `{parsed['scheme']}://` 本期不核验（显式披露，不进分母）"))
            continue
        if kind == "unparseable":
            unparseable += 1
            details.append(("unparseable", anchor, "不可解析"))
            continue
        if kind == "web":
            # ⛔ 只按 digest 取 bus 上的 transcript；parsed["uri"] 只用于报告，绝不去抓。
            web_parsed += 1
            body, err = store.body_for(parsed["digest"])
            if err:
                loud_failures.append((anchor, err))
                continue
            window, err = locate_range(body, parsed["range"])
            if err:
                loud_failures.append((anchor, err))
                continue
            q = norm(e["quote"])
            if q and q in norm(window):
                web_hit += 1
                details.append(("web-hit", anchor, "命中（离线比对 transcript）"))
            else:
                web_fail += 1
                details.append(("web-mismatch", anchor,
                                "引文不在 transcript 的指定 range（或为空白引文）"))
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
    sums_ok = (cur_parsed + web_parsed + old_count + unsupported_scheme
               + unparseable + discarded) == total and discarded == 0

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
            "unsupported_scheme": unsupported_scheme,
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
              "离线比对 bus 上的 transcript，⛔ 不抓 <uri>）")
        print(f"  旧格式 path:line: {old_count}   （不可校验 revision，独立计数）")
        print(f"  本期不核验的 scheme（unsupported_scheme）: {unsupported_scheme}   "
              "（显式披露，⛔ 不进命中率分母、不致非零退出）")
        print(f"  不可解析: {unparseable}   （独立计数）")
        print(f"  被丢弃（缺 anchor / 无法提取）: {discarded}   （⛔ 无声丢弃即失败）")
        print(f"  计数之和 === 输入条数: {sums_ok}")
        if loud_failures:
            print("\n【响亮失败】")
            for a, e in loud_failures:
                print(f"  {a}\n    -> {e}")
        if not sums_ok or cur_fail or web_fail:
            print("\n【未命中明细】")
            for kind, a, extra in details:
                if kind in ("current-mismatch", "web-mismatch", "old",
                            "unsupported-scheme", "unparseable"):
                    print(f"  {kind:<18} {a}  {extra}")

    # 退出码（v4 只扩展、不改既有含义）：
    #   0  → 无响亮失败 且 无现行/web 格式未命中（unsupported_scheme 本身不致非零）
    #   1  → 有现行格式或 web 格式引文未命中（校验失败）
    #   2  → 有响亮失败（缺 repo-root / 缺 transcript 源 / fetcher 取不到 / 形态不合理 / range 认不出）
    #   3  → 五类计数之和与输入条数不符（静默丢弃）
    if loud_failures:
        sys.exit(2)
    if not sums_ok:
        sys.exit(3)
    if cur_fail or web_fail:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
