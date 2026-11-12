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
