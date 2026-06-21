"""三轴契约 schema。一份契约一个 case：<case-id>.contract.yaml。
expect.{process,filesystem,semantic}；不变量：process/filesystem 至少一非空。"""
from dataclasses import dataclass, field
from pathlib import Path
import os, yaml

PROCESS_TYPES = {"skill_loaded", "tool_used", "tool_absent", "tool_count", "sequence"}
FS_TYPES = {"created", "modified", "deleted", "content", "unchanged_outside", "script"}
DEFAULT_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit", "Bash"]
DEFAULT_MODEL = "lingzhi/claude-opus-4-8"


class ContractError(Exception):
    pass


@dataclass
class Contract:
    skill: str
    path: Path
    case_id: str
    fixture: str = "kb"
    requires: list = field(default_factory=list)
    prompt: str = ""
    turns: list = field(default_factory=list)
    tools: list = field(default_factory=lambda: list(DEFAULT_TOOLS))
    model: str = DEFAULT_MODEL
    timeout: int = 600
    process: list = field(default_factory=list)
    filesystem: list = field(default_factory=list)
    semantic: dict | None = None


def _check_axis(entries, allowed, path, axis):
    for e in entries:
        if not isinstance(e, dict) or len(e) != 1:
            raise ContractError(f"{path}: {axis} entry must be single-key map: {e!r}")
        (typ,) = e.keys()
        if typ not in allowed:
            raise ContractError(f"{path}: unknown {axis} assert '{typ}'")


def load_contract(path: Path) -> Contract:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw.get("skill"):
        raise ContractError(f"{path}: missing 'skill'")
    trig = raw.get("trigger") or {}
    turns, prompt = trig.get("turns"), trig.get("prompt")
    if turns is not None:
        if prompt:
            raise ContractError(f"{path}: trigger has both prompt and turns")
        if not isinstance(turns, list) or not turns or not all(
                isinstance(t, str) and t.strip() for t in turns):
            raise ContractError(f"{path}: trigger.turns must be non-empty list of non-empty strings")
    elif not prompt:
        raise ContractError(f"{path}: missing trigger.prompt")
    exp = raw.get("expect") or {}
    process = exp.get("process") or []
    filesystem = exp.get("filesystem") or []
    semantic = exp.get("semantic")
    _check_axis(process, PROCESS_TYPES, path, "process")
    _check_axis(filesystem, FS_TYPES, path, "filesystem")
    # 不变量：至少一条确定性锚
    if not process and not filesystem:
        raise ContractError(f"{path}: needs >=1 process or filesystem assertion (invariant)")
    if semantic is not None and not isinstance(semantic.get("rubric"), str):
        raise ContractError(f"{path}: semantic.rubric must be a string")
    tools = trig.get("tools", list(DEFAULT_TOOLS))
    if not isinstance(tools, list):
        raise ContractError(f"{path}: trigger.tools must be a list")
    return Contract(
        skill=raw["skill"], path=Path(path),
        case_id=Path(path).name.removesuffix(".contract.yaml"),
        fixture=(raw.get("setup") or {}).get("fixture", "kb"),
        requires=(raw.get("setup") or {}).get("requires", []) or [],
        prompt=prompt or "", turns=turns or [], tools=tools,
        model=trig.get("model") or os.environ.get("KATANA_CONTRACT_MODEL") or DEFAULT_MODEL,
        timeout=int(trig.get("timeout", 600)),
        process=process, filesystem=filesystem, semantic=semantic,
    )


def discover_contracts(repo_root: Path) -> list:
    return [load_contract(p) for p in
            sorted(Path(repo_root).glob("plugins/*/tests/contracts/*.contract.yaml"))]
