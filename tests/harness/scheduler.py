"""case 级并行；exclusive:<name> 同组互斥（组内串行），其余全并行。"""
from concurrent.futures import ThreadPoolExecutor
import threading


def _exclusive_groups(requires: list) -> list[str]:
    """提取 requires 中所有 exclusive 组名。"""
    return [r.split(":", 1)[1] for r in requires if r.startswith("exclusive:")]


def schedule(jobs: list, jobs_n: int, requires_of) -> list:
    """并行调度 jobs，同一 exclusive 组内串行执行。

    Args:
        jobs: job 列表，每个 job 可调用
        jobs_n: 最大并行数（传给 ThreadPoolExecutor max_workers）
        requires_of: 函数，接收 job 返回其 requires 列表

    Returns:
        job 执行结果列表（保序）
    """
    # 为每个 exclusive 组建立锁
    locks: dict[str, threading.Lock] = {}
    for j in jobs:
        for g in _exclusive_groups(requires_of(j)):
            locks.setdefault(g, threading.Lock())

    def wrapped(j):
        """包装 job：执行前获取组锁，执行后释放。"""
        groups = sorted(_exclusive_groups(requires_of(j)))  # 排序防死锁
        acquired = []
        try:
            for g in groups:
                locks[g].acquire()
                acquired.append(g)
            return j()
        finally:
            for g in reversed(acquired):
                locks[g].release()

    with ThreadPoolExecutor(max_workers=jobs_n) as ex:
        return list(ex.map(wrapped, jobs))
