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

三类锚点**显式**分类（分别计数，总和必须 === 输入条数，任何一类不得静默丢弃）：
  1) 现行格式  code://<path>@<sha>#L<a>[-L<b>]   → 解析并**真正校验**（取该 revision 的文件，比对 quote）
  2) 旧格式    裸 path:line                       → 显式标为「旧格式，不可校验 revision」，**独立计数**
  3) 其它                                        → 显式标为「不可解析」, **独立计数**

fetcher 自检：取回内容必须**非空且形态合理**（行数 > 0 且能定位到给定行段），
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
# 旧格式：裸 path:line / path:lo-hi / path:a-b,c-d（历史 131 条）
OLD_URI_RE = re.compile(r'^(?!code://)(.+):\d+(?:[-,]\d+)*$')


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def load_entries(corpus):
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
        tok = open("/data/agent-bus/tokens/line-deep-research.token").read().strip()
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:7490/v1/channels/{ch}/messages?limit=1000",
            headers={"Authorization": f"Bearer {tok}"})
        ms = json.load(urllib.request.urlopen(req, timeout=30)).get("messages", [])
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
    """返回 (类别, 解析结果)。类别 ∈ {current, old, unparseable}。"""
    m = CURRENT_URI_RE.match(anchor)
    if m:
        return "current", {
            "path": m.group(1),
            "rev": m.group(2),
            "lo": int(m.group(3)),
            "hi": int(m.group(4)) if m.group(4) else int(m.group(3)),
        }
    m = OLD_URI_RE.match(anchor)
    if m:
        return "old", {"path": m.group(1)}
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


def main():
    ap = argparse.ArgumentParser(description="anchor-check v3 (N2b)")
    ap.add_argument("--corpus", required=True,
                    help="导出 JSON 文件路径，或 bus:<channel> 直连")
    ap.add_argument("--repo-root", default=None,
                    help="仓根（现行格式 locator 为仓内相对路径，必须外部提供）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    entries, discarded = load_entries(args.corpus)
    total = len(entries) + discarded

    cur_parsed = 0
    cur_hit = 0
    cur_fail = 0
    old_count = 0
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
        if kind == "unparseable":
            unparseable += 1
            details.append(("unparseable", anchor, "不可解析"))
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

    # 三类计数 + 被丢弃记录必须 === 输入总数；任何丢弃都意味着「无声丢弃」→ sums_ok=False
    sums_ok = (cur_parsed + old_count + unparseable + discarded) == total and discarded == 0

    if args.json:
        out = {
            "total": total,
            "current_parsed": cur_parsed,
            "current_verified_hit": cur_hit,
            "current_failed": cur_fail,
            "old_format": old_count,
            "unparseable": unparseable,
            "discarded": discarded,
            "sums_ok": sums_ok,
            "loud_failures": [{"anchor": a, "error": e} for a, e in loud_failures],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"输入条目总数: {total}")
        print(f"  现行格式已解析: {cur_parsed}   （真正校验命中 {cur_hit} / 未命中 {cur_fail}）")
        print(f"  旧格式 path:line: {old_count}   （不可校验 revision，独立计数）")
        print(f"  不可解析: {unparseable}   （独立计数）")
        print(f"  被丢弃（缺 anchor / 无法提取）: {discarded}   （⛔ 无声丢弃即失败）")
        print(f"  计数之和 === 输入条数: {sums_ok}")
        if loud_failures:
            print("\n【响亮失败】")
            for a, e in loud_failures:
                print(f"  {a}\n    -> {e}")
        if not sums_ok or cur_fail:
            print("\n【未命中明细】")
            for kind, a, extra in details:
                if kind in ("current-mismatch", "old", "unparseable"):
                    print(f"  {kind:<18} {a}  {extra}")

    # 退出码：
    #   0  → 无响亮失败 且 无现行格式未命中
    #   1  → 有现行格式引文未命中（校验失败）
    #   2  → 有响亮失败（缺 repo-root / fetcher 取不到 / 形态不合理）
    #   3  → 三类计数之和与输入条数不符（静默丢弃）
    if loud_failures:
        sys.exit(2)
    if not sums_ok:
        sys.exit(3)
    if cur_fail:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()