import time

from bili_agent.web import JobStore, SessionStore


def test_session_store_evicts_oldest_and_limits_history():
    store = SessionStore(ttl_seconds=60, max_items=2, max_history_turns=2)
    first = store.put(object())
    second = store.put(object())
    store._accessed[first] = time.time() - 1
    third = store.put(object())

    assert store.get(first) is None
    assert store.get(second) is not None
    assert store.get(third) is not None

    store.add_turn(third, "user", "one")
    store.add_turn(third, "assistant", "two")
    store.add_turn(third, "user", "three")
    assert [item["content"] for item in store.history(third)] == ["two", "three"]


def test_job_store_expires_only_terminal_jobs():
    jobs = JobStore(ttl_seconds=60, max_items=10)
    finished = jobs.create()
    jobs.update(finished, status="completed")
    jobs._updated[finished] = time.time() - 120

    running = jobs.create()
    jobs._updated[running] = time.time() - 120

    assert jobs.get(finished) is None
    assert jobs.get(running)["status"] == "queued"
