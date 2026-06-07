"""Contract loading & validation. One case per file: <case-id>.contract.yaml"""
from dataclasses import dataclass, field
from pathlib import Path
import yaml

ASSERT_TYPES = {"file_exists", "file_absent", "file_grep", "stdout_grep",
                "size_min", "json_path", "script"}
DEFAULT_TOOLS = ["Read", "Grep", "Glob", "Write", "Edit", "Bash"]


class ContractError(Exception):
    pass


@dataclass
class Contract:
    skill: str
    prompt: str
    path: Path
    case_id: str
    cwd: str = "kb"
    requires: list = field(default_factory=list)
    model: str = "lingzhi/claude-opus-4-8"
    permission_mode: str = "acceptEdits"
    allowed_tools: list = field(default_factory=lambda: list(DEFAULT_TOOLS))
    timeout: int = 600
    asserts: list = field(default_factory=list)
    verdict: dict | None = None


def load_contract(path: Path) -> Contract:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw.get("skill"):
        raise ContractError(f"{path}: missing required key 'skill'")
    inp = raw.get("input") or {}
    if not inp.get("prompt"):
        raise ContractError(f"{path}: missing input.prompt")
    asserts = raw.get("assert") or []  # YAML key 是保留字 assert，dataclass field 用 asserts
    verdict = raw.get("verdict")
    if not asserts and not verdict:
        raise ContractError(f"{path}: needs at least one of assert / verdict")
    if verdict is not None and not isinstance(verdict.get("rubric"), str):
        raise ContractError(f"{path}: verdict.rubric must be a non-empty string")
    for a in asserts:
        if not isinstance(a, dict) or len(a) != 1:
            raise ContractError(f"{path}: each assert entry must be a single-key map: {a!r}")
        (typ,) = a.keys()
        if typ not in ASSERT_TYPES:
            raise ContractError(f"{path}: unknown assert type '{typ}'")
    run = raw.get("run") or {}
    pre = raw.get("preconditions") or {}
    tools = run.get("allowed_tools", list(DEFAULT_TOOLS))
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ContractError(f"{path}: run.allowed_tools must be a list of strings")
    return Contract(
        skill=raw["skill"], prompt=inp["prompt"], path=Path(path),
        case_id=Path(path).name.removesuffix(".contract.yaml"),
        cwd=inp.get("cwd", "kb"), requires=pre.get("requires", []) or [],
        model=run.get("model", "lingzhi/claude-opus-4-8"),
        permission_mode=run.get("permission_mode", "acceptEdits"),
        allowed_tools=tools,
        timeout=int(run.get("timeout", 600)),
        asserts=asserts, verdict=verdict,
    )


def discover_contracts(repo_root: Path) -> list[Contract]:
    return [load_contract(p) for p in
            sorted(repo_root.glob("plugins/*/tests/contracts/*.contract.yaml"))]
