from pathlib import Path
from tempfile import TemporaryDirectory

from bili_agent.storage import SQLiteStore
from bili_agent.web import JobStore, SessionStore

from tests.test_skills import _result


def test_session_and_job_state_survive_store_recreation():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "state.sqlite3"
        first_storage = SQLiteStore(path)
        first_sessions = SessionStore(storage=first_storage)
        first_jobs = JobStore(storage=first_storage)
        session_id = first_sessions.put(_result())
        first_sessions.add_turn(session_id, "user", "这个视频讲了什么？")
        job_id = first_jobs.create()
        first_jobs.update(job_id, status="completed", stage="complete", progress=100, message="分析完成")
        cancelled_job_id = first_jobs.create()
        first_jobs.request_cancel(cancelled_job_id)
        first_storage.close()

        second_storage = SQLiteStore(path)
        second_sessions = SessionStore(storage=second_storage)
        second_jobs = JobStore(storage=second_storage)
        restored = second_sessions.get(session_id)

        assert restored is not None
        assert restored.metadata.title == "测试视频"
        assert second_sessions.history(session_id)[0]["content"] == "这个视频讲了什么？"
        assert second_jobs.get(job_id)["status"] == "completed"
        assert second_jobs.get(cancelled_job_id)["status"] == "cancelled"
        second_storage.close()
