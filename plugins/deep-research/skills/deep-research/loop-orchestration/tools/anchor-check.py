#!/usr/bin/env python3
"""锚点校验：quote 是否真的出现在 anchor 指的位置。纯确定性，无模型。"""
import json, os, re, subprocess, sys
from collections import Counter

CORPUS = sys.argv[1]

# 【可直接读 bus 全集，不必依赖手工导出的语料快照】
# 起因：本线曾用一份 114 条的导出语料下结论，而 bus 上实际有 131 条 —— 两个不同的集合，
# 而我一直以为是同一个。「我的数据集」与「全部数据」的差别，只在去比对时才显形。
# 用法：CORPUS 传 "bus:<evidence_channel>" 即直连取全集。
def _from_bus(spec):
    import urllib.request
    ch = spec.split(":", 1)[1]
    tok = open("/data/agent-bus/tokens/line-deep-research.token").read().strip()
    req = urllib.request.Request(
        f"http://127.0.0.1:7490/v1/channels/{ch}/messages?limit=1000",  # limit 必带
        headers={"Authorization": f"Bearer {tok}"})
    out = []
    for m in json.load(urllib.request.urlopen(req, timeout=30)).get("messages", []):
        pl = m.get("payload") or {}
        if pl.get("anchor") and pl.get("quote"):
            # 【必须把 credibility 一起取出来】2026-08-04 补:
            # 我改 worker.md 时宣称「credibility 变成会被机械证伪的声明」,
            # 但本工具当时**根本不读 credibility** —— 证伪的是引文,从不与【声称值】对照。
            # ⇒ 一个在坏引文上标 high 的 worker,不产生任何关联信号。
            # 这正是本线整晚在追的那类缺陷:**一个被宣称可检验的声明,而检验者不读那个声明。**
            out.append((f"seq{m.get('channel_seq')}", pl["anchor"], pl["quote"],
                        pl.get("credibility")))
    return out
COMMITS = {"/data/code/self/claude-web-gateway": (sys.argv[2] if len(sys.argv)>2 else None),
           "/data/code/self/loop-engine-supervisor-current": 'b503efc' or None}
ROOTS = {
    "claude-web-gateway": "/data/code/self/claude-web-gateway",
    "loop-engine-supervisor-current": "/data/code/self/loop-engine-supervisor-current",
    # 【2026-08-04 补】research:smoke-bus-semantics 的锚点全指 agent-bus 仓,
    # 而它不在本表里 => 17/17 报「文件不存在」。**那是仪器瞎了,不是语料造假。**
    # 我差点把它读成「该课题引文全部伪造」——本文件第 53 行的注释早就警告过同一件事,
    # 我还是又踩了一次。故补一道自检(见下 CONTROL),让仪器自己说「我瞎了」。
    "agent-bus": "/data/code/self/agent-bus",
}
DEFAULT_ROOT = ROOTS["claude-web-gateway"]

def norm(s):  # 空白归一，容忍换行/缩进重排
    return re.sub(r"\s+", " ", s).strip()

def read_file(repo, relpath, commit):
    try:
        if commit:
            return subprocess.run(["git", "-C", repo, "show", f"{commit}:{relpath}"],
                                  capture_output=True, text=True, timeout=20, check=True).stdout
        return open(f"{repo}/{relpath}", encoding="utf-8").read()
    except Exception:
        return None

def split_anchor(a):
    m = re.match(r"^(.*?):(\d+)(?:-(\d+))?$", a)
    if not m: return None
    path, lo, hi = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
    for name, root in ROOTS.items():
        if path.startswith(root):
            return root, path[len(root)+1:], lo, hi
    if path.startswith("/"): return None            # 绝对路径但不在已知仓
    # 【相对路径要试所有已知仓，不能静默回退到主仓】
    # 原实现直接落 DEFAULT_ROOT，于是 `loop-engine-supervisor-current/...` 这类
    # 带仓名前缀的相对路径被拿去 claude-web-gateway 里找 → 报「文件不存在」。
    # 那是【校验器的假阴性】，却会被读成「语料有假锚点」——错误归因，且方向最坏：
    # 冤枉产物。实测该文件真实存在。
    for root in ROOTS.values():
        if os.path.exists(os.path.join(root, path)): return root, path, lo, hi
        # 带仓名前缀：loop-engine-supervisor-current/xxx → <root>/xxx
        base = os.path.basename(root)
        if path.startswith(base + "/"):
            sub = path[len(base)+1:]
            if os.path.exists(os.path.join(root, sub)): return root, sub, lo, hi
    return DEFAULT_ROOT, path, lo, hi               # 都不命中才回退

# 【仪器自检:全员「文件不存在」= 仪器故障,不是语料结论】
# 判据来自本线反复踩的坑:任何「统计某物有多少异常」的检查,
# 必须能区分「样本真的异常」与「我根本没看到样本」。
# 100% 硬失败在真实语料里几乎不可能,而在 ROOTS 缺仓时必然发生。
def _instrument_guard(total, missing):
    if total and missing == total:
        print("\n🛑 **仪器自检未过**:全部 %d 条都报「文件不存在」。" % total)
        print("   这几乎必然是 ROOTS 缺少对应仓库,而不是语料全部伪造。")
        print("   **不要把本次输出当作语料结论。** 先补 ROOTS 再跑。")
        return False
    return True

if CORPUS.startswith("bus:"):
    entries = _from_bus(CORPUS)
    s = ""
else:
    s = open(CORPUS, encoding="utf-8").read()
    entries = []
for label, block in re.findall(r"### ([FE]\d+)\s+\[[^\]]*\]\n```json\n(\{.*?\n\})\n```", s, re.S):
    try: d = json.loads(block)
    except Exception: continue
    if d.get("anchor") and d.get("quote"):
        entries.append((label, d["anchor"], d["quote"], d.get("credibility")))

res = Counter(); details = []
# 【声称 vs 实测】credibility 是生产者的自评;本表把它与机械判定对照。
# 只有对照才让自评承重 —— 否则它只是一个没人读的标签。
cred = Counter()          # (声称值, 是否验过) -> 计数
overclaim = []            # 声称 high 却验不过的条目
for label, anchor, quote, claimed in entries:
    sp = split_anchor(anchor)
    if not sp:
        res["锚点格式不可解析"] += 1; details.append(("锚点格式不可解析", label, anchor, "")); continue
    repo, rel, lo, hi = sp
    content = read_file(repo, rel, COMMITS.get(repo))
    if content is None:
        res["文件不存在"] += 1; details.append(("文件不存在", label, anchor, "")); continue
    lines = content.splitlines()
    window = norm("\n".join(lines[max(0, lo-1):hi]))
    q = norm(quote)
    if q and q in window:
        res["✅ 命中指定行段"] += 1
        cred[(claimed or "(未填)", "验得过")] += 1
    elif q and q in norm(content):
        # 找出实际所在行
        for i in range(len(lines)):
            for j in range(i+1, min(i+80, len(lines))+1):
                if q in norm("\n".join(lines[i:j])):
                    details.append(("⚠️ 行段漂移", label, anchor, f"实际约在 {i+1}-{j}")); break
            else: continue
            break
        res["⚠️ 行段漂移（引文在文件里但不在指定行）"] += 1
        cred[(claimed or "(未填)", "引文在但行号错")] += 1
        if (claimed or "").lower() == "high":
            overclaim.append(("行段漂移", label, anchor, claimed))
    else:
        # 【分岔点定位】把「引文不在该文件中」拆成两类 —— 它们的成因完全不同：
        #   · 前缀几乎不匹配 → 引文与该文件不沾边（锚点指错文件 / 大范围改写）
        #   · 前缀大段匹配后偏离 → **编造的指纹**：前半照抄、后半自己编
        # 后者最危险：它通过任何「引文里有没有关键词」式的粗检查，
        # 且实测编掉的往往是【错误处理】—— 研究结论最爱引用的那部分。
        nc = norm(content)
        lo_, hi_ = 0, len(q)
        while lo_ < hi_:
            mid = (lo_ + hi_ + 1) // 2
            if q[:mid] in nc: lo_ = mid
            else: hi_ = mid - 1
        ratio = lo_ / len(q) if q else 0
        if ratio >= 0.5:
            res["🔴 前缀匹配后偏离（编造指纹）"] += 1
            cred[(claimed or "(未填)", "硬失败")] += 1
            if (claimed or "").lower() == "high":
                overclaim.append(("编造指纹", label, anchor, claimed))
            i = nc.find(q[:lo_]) if lo_ else -1
            actual = nc[i+lo_:i+lo_+70] if i >= 0 else ""
            details.append(("🔴 前缀匹配后偏离（编造指纹）", label, anchor,
                            f"前 {lo_}/{len(q)} 字符({ratio:.0%})逐字正确，之后偏离\n"
                            f"        引文续: {q[lo_:lo_+70]}\n"
                            f"        文件续: {actual}"))
        else:
            res["❌ 引文与该文件不沾边"] += 1
            cred[(claimed or "(未填)", "硬失败")] += 1
            if (claimed or "").lower() == "high":
                overclaim.append(("不沾边", label, anchor, claimed))
            details.append(("❌ 引文与该文件不沾边", label, anchor,
                            f"最长匹配前缀仅 {lo_}/{len(q)} 字符({ratio:.0%})"))

print(f"语料带 anchor+quote 条目：{len(entries)}   各仓代码状态：{COMMITS}")
print("=" * 72)
for k, v in res.most_common():
    print(f"  {v:4d}  {k}")
print("=" * 72)
bad = [d for d in details if d[0] != "⚠️ 行段漂移"]
if bad:
    print("\n【硬失败明细】")
    for kind, label, anchor, extra in bad[:25]:
        print(f"  {kind}  {label}  {anchor}")
        if extra: print(f"        引文: {extra}")
drift = [d for d in details if d[0] == "⚠️ 行段漂移"][:8]
if drift:
    print("\n【行段漂移样例（前 8）】")
    for _, label, anchor, extra in drift:
        print(f"  {label}  {anchor}  → {extra}")

# ── 声称 vs 实测（本工具此前完全没做的那一半）──
print("\n" + "=" * 72)
print("【credibility 声称 vs 机械实测】")
if not cred:
    print("  语料里没有 credibility 字段 —— 无法对照。")
    print("  注意：**这不等于「都填对了」**，是「压根没有可对照的声明」。")
else:
    for (c, v), n in sorted(cred.items()):
        print(f"  声称 {c:<8s} × 实测 {v:<12s} : {n}")
    print()
    if overclaim:
        print(f"  🔴 **声称 high 但验不过：{len(overclaim)} 条** —— 自评与实测直接冲突")
        for kind, label, anchor, c in overclaim[:10]:
            print(f"       [{kind}] {label}  {anchor[:70]}")
        if len(overclaim) > 10:
            print(f"       …另 {len(overclaim)-10} 条")
        print()
        print("  ⇒ 这些是【可归因到具体条目】的过度声称。credibility 因此不再是标签，")
        print("     而是一个会被本工具当场对上的断言。")
    elif all(c == "(未填)" for (c, _) in cred):
        # 【绝不能报 ✅】全部未填 = 没有可对照的声明,不是「都填对了」。
        # 实测根因:credibility 是 research.finding.v1 的字段,
        # 而 anchor+quote 在 research.excerpt.v1 上 —— **两个不同的消息**。
        # 本节按 excerpt 取 credibility 永远取不到 ⇒ 结论恒为「干净」。
        # 这是本线今晚删过一次的同型缺陷:**零功率检查,其缺席被读成证据**。
        print("  🛑 **全部条目都没有 credibility 字段 —— 本节无结论。**")
        print("     根因：credibility 属 research.finding.v1，anchor/quote 属 research.excerpt.v1，")
        print("     二者是不同消息。按 excerpt 取 credibility 恒为空。")
        print("     ⇒ **不要把这读成「没有过度声称」** —— 是压根没有可对照的声明。")
        print("     正确做法见下方【按 finding 归并】一节。")
    else:
        print("  ✅ 无「声称 high 却验不过」的条目。")

# ── 【按 finding 归并】credibility 的正确对照层 ──
# credibility 在 finding 上,anchor/quote 在 excerpt 上,excerpt 经 refs 指向 finding。
# ⇒ 对照必须跨消息 join,不能在单条 excerpt 上做。这是本工具此前缺的那一半。
if CORPUS.startswith("bus:") and ".evidence" in CORPUS:
    import sqlite3, urllib.request as _u
    topic = CORPUS.split(":", 1)[1].rsplit(".evidence", 1)[0]
    try:
        _tok = open("/data/agent-bus/tokens/line-deep-research.token").read().strip()
        def _get(ch):
            r = _u.Request(f"http://127.0.0.1:7490/v1/channels/{ch}/messages?limit=1000",
                           headers={"Authorization": f"Bearer {_tok}"})
            return json.load(_u.urlopen(r, timeout=30)).get("messages", [])
        _db = sqlite3.connect("/data/agent-bus/state/bus.sqlite3")
        _idx = _get(f"{topic}.index")
        _fmap = {m["entity_id"]: (m.get("payload") or {})
                 for m in _idx if (m.get("kind") or "").startswith("research.finding")}
        _sup = {}
        for m in _get(f"{topic}.evidence"):
            pl = m.get("payload") or {}
            if not (pl.get("anchor") and pl.get("quote")):
                continue
            sp = split_anchor(pl["anchor"])
            good = False
            if sp:
                repo, rel, lo, hi = sp
                c = read_file(repo, rel, COMMITS.get(repo))
                good = bool(c and norm(pl["quote"]) in norm(c))
            for (tgt,) in _db.execute(
                    "SELECT target_entity FROM message_refs WHERE message_id=?", (m["message_id"],)):
                if tgt in _fmap:
                    g, t = _sup.get(tgt, (0, 0))
                    _sup[tgt] = (g + (1 if good else 0), t + 1)
        print("\n" + "=" * 72)
        print("【按 finding 归并：声称 credibility vs 支撑引文实测】")
        rows = []
        for e, (g, t) in _sup.items():
            rows.append(((_fmap[e].get("credibility") or "(未填)"), g, t, _fmap[e].get("title", "")))
        agg = Counter()
        for c, g, t, _ in rows:
            agg[(c, "全部验得过" if g == t else ("部分" if g else "一条都验不过"))] += 1
        for k, n in sorted(agg.items()):
            print(f"  声称 {k[0]:<8s} × {k[1]:<12s} : {n}")
        bad = [r for r in rows if r[1] == 0]
        if bad:
            print(f"\n  🔴 **{len(bad)} 条 finding 的支撑引文一条都验不过**：")
            for c, g, t, title in bad:
                print(f"       credibility={c}  引文 {t} 条全挂  {title[:56]}")
            print("\n  ⇒ 这才是 credibility 能被对上的位置。")
    except Exception as _e:  # noqa: BLE001
        print(f"\n（按 finding 归并失败，跳过：{type(_e).__name__}: {_e}）")
