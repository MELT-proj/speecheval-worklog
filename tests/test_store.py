from worklog.store import Store

from .helpers import run


def test_sync_and_version_stable(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    v1 = s.sync([run("a.json", created="2026-11-12T10:00:00", status="success")])
    assert s.sync([run("a.json", created="2026-11-12T10:00:00", status="success")]) == v1
    v2 = s.sync([run("a.json", created="2026-11-12T10:00:00", status="success", mtime_ns=2)])
    assert v2 != v1


def test_newest_success_wins_and_error_never_shadows(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync(
        [
            run("old.json", created="2026-11-12T09:00:00", status="success", metrics={"corpus_bleu": 20.0}),
            run("new.json", created="2026-11-12T10:00:00", status="success", metrics={"corpus_bleu": 24.0}),
            run("err.json", created="2026-11-12T11:00:00", status="error", metrics={}),
        ]
    )
    win = s.winning()
    assert len(win) == 1 and win[0].metrics["corpus_bleu"] == 24.0
    assert [r.status for r in s.history(limit=10)] == ["error", "success", "success"]


def test_deleted_log_is_removed(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync([run("a.json"), run("b.json", slice="asr-en", task="asr")])
    s.sync([run("a.json")])
    assert [r.path for r in s.all()] == ["a.json"]


def test_count_by_status(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync([run("a.json"), run("b.json", status="error"), run("c.json", status="error")])
    assert s.count(status="error") == 2
    assert s.count() == 3
