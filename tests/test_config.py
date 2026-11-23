import pytest

from worklog.config import load_config


def test_defaults_load_shipped_yaml():
    cfg = load_config()
    assert cfg.port == 8765 and cfg.host == "127.0.0.1"
    assert cfg.aggregate == "macro"
    assert cfg.roots[0].name == "eval-runs"


def test_cache_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKLOG_CACHE", str(tmp_path / "w.sqlite"))
    p = tmp_path / "c.yaml"
    p.write_text("roots: [/x]\ncache: ${WORKLOG_CACHE}\n")
    assert load_config(p).cache == tmp_path / "w.sqlite"


def test_metric_lookup(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "roots: [/x]\n"
        "metrics:\n  corpus_wer_raw: {label: WER, lower: true}\n"
        "headline: {asr: corpus_wer_raw}\n"
    )
    cfg = load_config(p)
    assert cfg.headline_metric("asr").label == "WER"
    assert cfg.headline_metric("st") is None
    assert cfg.metric_names("asr") == ("corpus_wer_raw",)


def test_partial_file_keeps_shipped_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("roots: [/x]\n")
    cfg = load_config(p)
    assert cfg.families and cfg.headline_metric("asr").label == "WER"


def test_worklog_cache_env_beats_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKLOG_CACHE", str(tmp_path / "env.sqlite"))
    assert load_config().cache == tmp_path / "env.sqlite"


def test_missing_explicit_config_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_trends_lineages_parse_ids_names_and_pattern_lists(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "trends:\n"
        "  lineages:\n"
        "    - {id: smurf/16n, name: SMURF 16n, match: '^eurollm'}\n"
        "    - {name: SMURF batch, match: ['^step-', '^batch-']}\n"
    )
    cfg = load_config(path)
    assert [lineage.id for lineage in cfg.lineages] == ["smurf/16n", "smurf-batch"]
    assert cfg.lineage_for("eurollm-speech-16n-step-0108000").id == "smurf/16n"
    assert cfg.lineage_for("batch-3000").id == "smurf-batch"
    assert cfg.lineage_for("canary-1b-v2") is None


def test_trends_lineage_regex_must_compile(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("trends:\n  lineages:\n    - {name: Broken, match: '[unclosed'}\n")
    with pytest.raises(ValueError, match="Broken"):
        load_config(path)
