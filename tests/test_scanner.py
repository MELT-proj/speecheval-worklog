from worklog.scanner import direction_of, model_family, parse_pair, scan_run

from .helpers import make_log


def test_success_st_fields(tmp_path):
    p = make_log(tmp_path)
    run = scan_run(p)
    assert (run.campaign, run.model, run.benchmark, run.slice, run.task) == (
        "speechval-canary",
        "canary-1b-v2",
        "fleurs24-st-xen-dev",
        "st-fleurs-de_en",
        "st",
    )
    assert (run.src, run.tgt) == ("de", "en")
    assert direction_of(run.src, run.tgt) == "xen"
    assert run.metrics == {"corpus_bleu": 24.5}
    assert run.status == "success" and run.finished == "2026-11-12T10:05:00+00:00"


def test_metric_objects_are_unwrapped(tmp_path):
    assert scan_run(make_log(tmp_path, metrics={"corpus_bleu": 12.5})).metrics["corpus_bleu"] == 12.5


def test_pending_post_scorer(tmp_path):
    p = make_log(tmp_path, post_scorers=("melteval/rescore.py@metricx_scorer",), metrics={"corpus_bleu": 24.5})
    assert scan_run(p).pending == ("metricx_scorer",)


def test_error_log_has_no_samples_and_is_extracted(tmp_path):
    p = make_log(tmp_path, status="error", metrics={}, samples=0)
    run = scan_run(p)
    assert run.status == "error" and run.finished is None


def test_unrelated_json_is_ignored(tmp_path):
    p = tmp_path / "something.json"
    p.write_text('{"hello": "world"}')
    assert scan_run(p) is None


def test_half_written_file_is_ignored(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text('{"eval": {"task":')
    assert scan_run(p) is None


def test_region_codes_and_missing_task_args(tmp_path):
    assert parse_pair("covost2-en_zh-CN-dev", None, "st", None) == ("en", "zh-CN")
    assert parse_pair(None, "st-fleurs-de_en", "st", None) == ("de", "en")
    assert parse_pair(None, "asr-bg", "asr", {"lang": "bg"}) == (None, None)


def test_model_family():
    assert model_family("melt/hf/canary") == "baseline"
    assert model_family("melt/checkpoints/x") == "melt"
    assert model_family("smurf//e/x.ckpt") == "smurf"


def test_language_field_only_for_asr(tmp_path):
    st = scan_run(make_log(tmp_path))
    assert st.lang is None
    asr = scan_run(
        make_log(
            tmp_path,
            benchmark="voxpopuli-dev",
            slice_id="asr-en",
            task="asr",
            dataset_id="voxpopuli-en-dev",
            lang="en",
            src=None,
            tgt=None,
        )
    )
    assert asr.lang == "en"
