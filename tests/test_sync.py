from dataclasses import replace

from worklog.store import Store
from worklog.sync import sync_store

from .helpers import make_log


def test_only_changed_logs_are_parsed(tmp_path, cfg):
    make_log(tmp_path, slice_id="st-fleurs-de_en")
    make_log(tmp_path, slice_id="st-fleurs-fr_en", src="fr")
    config = replace(cfg, roots=(tmp_path,), include=("*",), cache=tmp_path / "w.sqlite")
    store = Store(config.cache)
    assert sync_store(config, store).parsed == 2
    assert sync_store(config, store).parsed == 0
    assert len(store.all()) == 2


def test_touched_and_deleted_logs(tmp_path, cfg):
    first = make_log(tmp_path, slice_id="st-fleurs-de_en")
    second = make_log(tmp_path, slice_id="st-fleurs-fr_en", src="fr")
    config = replace(cfg, roots=(tmp_path,), include=("*",), cache=tmp_path / "w.sqlite")
    store = Store(config.cache)
    sync_store(config, store)

    first.write_text(first.read_text())  # same bytes, new mtime
    assert sync_store(config, store).parsed == 1

    second.unlink()
    sync_store(config, store)
    assert [row.slice for row in store.all()] == ["st-fleurs-de_en"]


def test_truncated_log_warns_and_is_retried(tmp_path, cfg):
    from dataclasses import replace as dc_replace

    make_log(tmp_path, slice_id="st-fleurs-de_en")
    broken = tmp_path / "logs" / "canary-1b-v2" / "fleurs24-st-xen-dev" / "st-fleurs-fr_en" / "broken.json"
    broken.parent.mkdir(parents=True)
    broken.write_text('{"eval": {"task":')
    config = dc_replace(cfg, roots=(tmp_path,), include=("*",), cache=tmp_path / "w.sqlite")
    store = Store(config.cache)
    first = sync_store(config, store)
    assert (first.parsed, first.warnings) == (2, 1)
    assert len(store.all()) == 1

    broken.write_text('{"eval": {"task": "x"}}')
    second = sync_store(config, store)
    assert second.warnings == 0


def test_unrelated_json_does_not_warn(tmp_path, cfg):
    from dataclasses import replace as dc_replace

    (tmp_path / "campaign").mkdir()
    (tmp_path / "campaign" / "config.json").write_text("{not json")
    config = dc_replace(cfg, roots=(tmp_path,), include=("campaign",), cache=tmp_path / "w.sqlite")
    assert sync_store(config, Store(config.cache)).warnings == 0
