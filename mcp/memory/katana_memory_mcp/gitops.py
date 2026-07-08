"""数据 repo 的 auto-commit。写入成功优先：git 任何失败都降级为未提交，不 raise。"""
import subprocess


def _run(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo_root, *args],
                          capture_output=True, text=True, timeout=30)


def commit(repo_root: str, message: str, paths: list[str]) -> dict:
    try:
        add = _run(repo_root, "add", "-A", "--", *paths)
        if add.returncode != 0:
            return {"committed": False, "detail": add.stderr.strip()}
        diff = _run(repo_root, "diff", "--cached", "--quiet")
        if diff.returncode == 0:
            return {"committed": False, "detail": "nothing to commit"}
        c = _run(repo_root, "commit", "-m", message)
        if c.returncode != 0:
            return {"committed": False, "detail": c.stderr.strip() or c.stdout.strip()}
        return {"committed": True, "detail": c.stdout.strip().splitlines()[0] if c.stdout.strip() else "committed"}
    except (subprocess.SubprocessError, OSError) as e:
        return {"committed": False, "detail": str(e)}
