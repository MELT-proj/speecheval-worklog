from dataclasses import replace

from worklog.store import Store
from worklog.sync import sync_store

from .helpers import make_log


def test_only_changed_logs_are_parsed(tmp_path, cfg):
    make_log(tmp_path, slice_id="st-fleurs-de_en")
    make_log(tmp_path, slice_id="st-fleurs-fr_en", src="fr")
    config = replace(cfg, roots=(tmp_path,), include=("*",), cache=tmp_path / "w.sqlite")
    store = Store(config.cache)
    assert sync_store(config, store) == 2
    assert sync_store(config, store) == 0
    assert len(store.all()) == 2


def test_touched_and_deleted_logs(tmp_path, cfg):
    first = make_log(tmp_path, slice_id="st-fleurs-de_en")
    second = make_log(tmp_path, slice_id="st-fleurs-fr_en", src="fr")
    config = replace(cfg, roots=(tmp_path,), include=("*",), cache=tmp_path / "w.sqlite")
    store = Store(config.cache)
    sync_store(config, store)

    first.write_text(first.read_text())  # same bytes, new mtime
    assert sync_store(config, store) == 1

    second.unlink()
    sync_store(config, store)
    assert [row.slice for row in store.all()] == ["st-fleurs-de_en"]
