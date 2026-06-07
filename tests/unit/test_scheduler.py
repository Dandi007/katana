import threading, time
from harness.scheduler import schedule


def test_parallel_and_exclusive_grouping():
    running, max_seen, lock = set(), [0], threading.Lock()
    spans = {}   # name -> (start, end)

    def make(name, requires):
        def job():
            t_start = time.monotonic()
            with lock:
                running.add(name); max_seen[0] = max(max_seen[0], len(running))
            time.sleep(0.05)
            with lock:
                running.discard(name)
            spans[name] = (t_start, time.monotonic())
            return name
        job.requires = requires
        return job

    jobs = [make("a", []), make("b", []),
            make("x1", ["exclusive:chrome"]), make("x2", ["exclusive:chrome"])]
    results = schedule(jobs, jobs_n=4,
                       requires_of=lambda j: j.requires)
    assert sorted(results) == ["a", "b", "x1", "x2"]
    assert max_seen[0] >= 2                       # 确有并行
    # x1/x2 时间区间不重叠（组锁互斥）
    (s1, e1), (s2, e2) = spans["x1"], spans["x2"]
    assert e1 <= s2 or e2 <= s1


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
