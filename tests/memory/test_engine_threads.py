"""The memory engine is built once per process, by whichever thread asks
first. Web requests run on threads, so every other caller has to wait for the
whole engine rather than see it published before its schema and its session
factory exist."""

import threading

from sqlalchemy import text

from lib.memory.engine import MemorySessionLocal

THREADS = 8


def test_concurrent_first_sessions_all_get_a_working_engine():
    start = threading.Barrier(THREADS)
    errors = []

    def first_session():
        start.wait()
        try:
            with MemorySessionLocal() as session:
                session.execute(text("SELECT COUNT(*) FROM memories")).scalar_one()
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=first_session) for _ in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
