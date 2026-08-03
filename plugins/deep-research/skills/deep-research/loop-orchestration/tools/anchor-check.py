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
            out.append((f"seq{m.get('channel_seq')}", pl["anchor"], pl["quote"]))
    return out
COMMITS = {"/data/code/self/claude-web-gateway": (sys.argv[2] if len(sys.argv)>2 else None),
           "/data/code/self/loop-engine-supervisor-current": 'b503efc' or None}
ROOTS = {
    "claude-web-gateway": "/data/code/self/claude-web-gateway",
    "loop-engine-supervisor-current": "/data/code/self/loop-engine-supervisor-current",
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
        entries.append((label, d["anchor"], d["quote"]))

res = Counter(); details = []
for label, anchor, quote in entries:
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
    elif q and q in norm(content):
        # 找出实际所在行
        for i in range(len(lines)):
            for j in range(i+1, min(i+80, len(lines))+1):
                if q in norm("\n".join(lines[i:j])):
                    details.append(("⚠️ 行段漂移", label, anchor, f"实际约在 {i+1}-{j}")); break
            else: continue
            break
        res["⚠️ 行段漂移（引文在文件里但不在指定行）"] += 1
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
            i = nc.find(q[:lo_]) if lo_ else -1
            actual = nc[i+lo_:i+lo_+70] if i >= 0 else ""
            details.append(("🔴 前缀匹配后偏离（编造指纹）", label, anchor,
                            f"前 {lo_}/{len(q)} 字符({ratio:.0%})逐字正确，之后偏离\n"
                            f"        引文续: {q[lo_:lo_+70]}\n"
                            f"        文件续: {actual}"))
        else:
            res["❌ 引文与该文件不沾边"] += 1
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
