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


def test_same_slice_in_two_campaigns_is_one_winner(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync([
        run("a.json", campaign="campaign-a", created="2026-11-12T09:00:00", metrics={"corpus_bleu": 20.0}),
        run("b.json", campaign="campaign-b", created="2026-11-12T10:00:00", metrics={"corpus_bleu": 24.0}),
    ])
    win = s.winning()
    assert len(win) == 1 and win[0].campaign == "campaign-b"


def test_newest_breaks_created_ties_on_mtime(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync([
        run("a.json", mtime_ns=1, created="2026-11-12T10:00:00", metrics={"corpus_bleu": 20.0}),
        run("b.json", mtime_ns=2, created="2026-11-12T10:00:00", metrics={"corpus_bleu": 24.0}),
    ])
    assert s.winning()[0].metrics["corpus_bleu"] == 24.0


def test_open_store_falls_back_when_path_is_unwritable(tmp_path):
    from worklog.store import open_store

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    store = open_store(blocker / "w.sqlite", fallback_dir=tmp_path / "fallback")
    assert store.path == tmp_path / "fallback" / "worklog.sqlite"
    assert store.all() == []


def test_history_filters_by_benchmark_set(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync([run("a.json", benchmark="a"), run("b.json", benchmark="b"), run("c.json", benchmark="c")])
    assert {r.benchmark for r in s.history(limit=10)} == {"a", "b", "c"}
    assert {r.benchmark for r in s.history(limit=10, benchmarks={"a", "b"})} == {"a", "b"}
    assert {r.benchmark for r in s.history(limit=10, exclude_benchmarks={"a"})} == {"b", "c"}
    assert s.history(limit=10, benchmarks=set()) == []
    assert {r.benchmark for r in s.history(limit=10, exclude_benchmarks=set())} == {"a", "b", "c"}


def test_count_filters_by_benchmark_set(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    s.sync(
        [
            run("a.json", benchmark="a", status="error"),
            run("b.json", benchmark="b", status="error"),
            run("c.json", benchmark="c", status="success"),
        ]
    )
    assert s.count(status="error") == 2
    assert s.count(status="error", benchmarks={"a"}) == 1
    assert s.count(status="error", exclude_benchmarks={"a"}) == 1
