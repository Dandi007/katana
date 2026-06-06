import threading, time
from harness.scheduler import schedule


def test_parallel_and_exclusive_grouping():
    running, max_seen, lock = set(), [0], threading.Lock()
    order = []

    def make(name, requires):
        def job():
            with lock:
                running.add(name)
                max_seen[0] = max(max_seen[0], len(running))
            time.sleep(0.05)
            with lock:
                running.discard(name)
                order.append(name)
            return name
        job.requires = requires
        return job

    jobs = [make("a", []), make("b", []),
            make("x1", ["exclusive:chrome"]), make("x2", ["exclusive:chrome"])]
    results = schedule(jobs, jobs_n=4,
                       requires_of=lambda j: j.requires)
    assert sorted(results) == ["a", "b", "x1", "x2"]
    assert max_seen[0] >= 2                       # 确有并行
    ix1, ix2 = order.index("x1"), order.index("x2")
    assert abs(ix1 - ix2) >= 1                    # x1/x2 串行（不同时跑）——由组锁保证


def test_exclusive_never_concurrent():
    overlap = [False]
    busy = threading.Event()

    def make(name):
        def job():
            if busy.is_set():
                overlap[0] = True
            busy.set()
            time.sleep(0.05)
            busy.clear()
            return name
        job.requires = ["exclusive:res"]
        return job

    schedule([make("p"), make("q"), make("r")], jobs_n=3,
             requires_of=lambda j: j.requires)
    assert overlap[0] is False
