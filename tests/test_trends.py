from __future__ import annotations

import dataclasses

from worklog.config import Lineage
from worklog.trends import build_languages, build_trends, fold_runs, step_of

from .helpers import run


def test_step_of_handles_padded_bare_and_plain_names():
    assert step_of("fondue-step-3000") == (3000, "fondue")
    assert step_of("eurollm-speech-16n-step-0108000") == (108000, "eurollm-speech-16n")
    assert step_of("step-108000") == (108000, "step")
    assert step_of("canary-1b-v2") == (None, "canary-1b-v2")


def test_bare_step_names_fold_into_one_lineage(cfg):
    runs = [
        run(model="step-108000", provider="smurf//batch/step=108000.ckpt"),
        run(model="step-114000", provider="smurf//batch/step=114000.ckpt"),
        run(model="eurollm-speech-16n-step-0108000", provider="smurf//sweep/step=108000.ckpt"),
    ]
    payload = build_trends(cfg, runs)
    assert [lineage["id"] for lineage in payload["lineages"]] == ["smurf/16n", "smurf/batch"]
    batch = next(lineage for lineage in payload["lineages"] if lineage["id"] == "smurf/batch")
    assert batch["steps"] == [108000, 114000]
    assert batch["max_step"] == 114000


def test_one_config_entry_can_merge_several_model_prefixes(cfg):
    merged = dataclasses.replace(
        cfg,
        lineages=(Lineage(id="smurf/all", name="SMURF all", patterns=("^eurollm", "^step-")),),
    )
    runs = [
        run(model="step-108000", provider="smurf//batch/step=108000.ckpt"),
        run(model="eurollm-speech-16n-step-0108000", provider="smurf//sweep/step=108000.ckpt"),
    ]
    assert {record.lineage.id for record in fold_runs(merged, runs)} == {"smurf/all"}


def test_two_names_for_one_slice_and_step_keep_the_newest(cfg):
    common = {
        "provider": "smurf//x",
        "benchmark": "polyspeech-100-pa_qt-200",
        "slice": "audio_mcq-polyspeech-pa_qt-de",
        "task": "audio_mcq",
        "src": None,
        "tgt": None,
        "lang": "de",
    }
    runs = [
        run(model="eurollm-speech-16n-step-0600000", created="2026-11-18T10:00:00+00:00",
            metrics={"choice_accuracy": 0.40}, **common),
        run(model="eurollm-speech-16n-step-600000", created="2026-11-19T10:00:00+00:00",
            metrics={"choice_accuracy": 0.50}, **common),
    ]
    records = fold_runs(cfg, runs)
    assert len(records) == 1
    assert records[0].run.metrics["choice_accuracy"] == 0.50


def test_fold_skips_errors_unclassified_and_unstepped_runs(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000", status="error"),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            benchmark="something-new", slice="x"),
        run(model="canary-1b-v2", provider="melt/hf/canary"),
    ]
    assert fold_runs(cfg, runs) == []


def _panel(payload, family):
    return next(panel for panel in payload["panels"] if panel["family"] == family)


def _point(payload, family, lineage, step):
    return next(
        point
        for point in _panel(payload, family)["points"]
        if point["lineage"] == lineage and point["step"] == step
    )


def test_panel_aggregates_slices_and_reports_per_metric_coverage(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-de_en", src="de", tgt="en",
            metrics={"corpus_bleu": 20.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-fr_en", src="fr", tgt="en",
            metrics={"corpus_chrf": 50.0}, pending=("metricx_scorer",)),
    ]
    payload = build_trends(cfg, runs)
    point = _point(payload, "FLEURS XtoEN", "melt/fondue", 3000)
    assert point["values"]["corpus_bleu"] == 20.0
    assert point["coverage"]["corpus_bleu"] == {"n": 1, "total": 2, "status": "partial"}
    assert point["coverage"]["metricx"] == {"n": 0, "total": 2, "status": "pending"}
    assert "metricx" not in point["values"]
    assert _panel(payload, "FLEURS XtoEN")["languages"] == ["de", "fr"]


def test_panel_uses_the_configured_aggregate(cfg):
    weighted = dataclasses.replace(cfg, aggregate="weighted")
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-de_en", src="de", tgt="en", total_samples=1,
            metrics={"corpus_bleu": 10.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-fr_en", src="fr", tgt="en", total_samples=3,
            metrics={"corpus_bleu": 20.0}),
    ]
    point = _point(build_trends(weighted, runs), "FLEURS XtoEN", "melt/fondue", 3000)
    assert point["values"]["corpus_bleu"] == 17.5
    point = _point(build_trends(cfg, runs), "FLEURS XtoEN", "melt/fondue", 3000)
    assert point["values"]["corpus_bleu"] == 15.0


def test_checkpoint_feed_has_headline_values_and_slice_counts(cfg):
    cfg = dataclasses.replace(cfg, trend_metrics={})
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-de_en", src="de", tgt="en", metrics={"corpus_bleu": 20.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            slice="st-fleurs-fr_en", src="fr", tgt="en", metrics={"corpus_bleu": 30.0}),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000",
            benchmark="voxpopuli-dev", slice="asr-voxpopuli-de", task="asr",
            src=None, tgt=None, lang="de", metrics={"corpus_wer_raw": 0.12}),
    ]
    payload = build_trends(cfg, runs)
    row = next(row for row in payload["checkpoints"] if row["step"] == 3000)
    assert row["model"] == "fondue-step-3000"
    assert row["values"] == {"FLEURS XtoEN": 25.0, "VoxPopuli": 0.12}
    assert row["coverage"] == {"FLEURS XtoEN": 2, "VoxPopuli": 1}


def _bleu_runs(step_values, lang="de"):
    return [
        run(
            model=f"eurollm-speech-16n-step-{step:06d}",
            provider="smurf//sweep/step.ckpt",
            benchmark="fleurs24-st-xen-dev",
            slice=f"st-fleurs-{lang}_en",
            task="st",
            src=lang,
            tgt="en",
            metrics={"corpus_bleu": value},
            created=f"2026-11-{10 + index:02d}T10:00:00+00:00",
        )
        for index, (step, value) in enumerate(step_values)
    ]


def test_languages_rank_worst_in_the_metric_direction(cfg):
    runs = (
        _bleu_runs([(102000, 30.0), (162000, 32.0), (270000, 31.0)], "de")
        + _bleu_runs([(102000, 20.0), (162000, 10.0), (270000, 9.0)], "mt")
    )
    body = build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu", threshold=0.1, n=1)
    assert len(body["points"]) == 6
    assert body["worst_now"][0]["lang"] == "mt"
    assert body["worst_now"][0]["latest"] == 9.0
    assert body["worst_now"][0]["latest_step"] == 270000
    assert body["regressions"][0]["lang"] == "mt"
    assert body["regressions"][0]["onset"] == 162000


def test_lower_is_better_flips_the_worst_language(cfg):
    runs = []
    for step, de, fr in [(3000, 0.10, 0.20), (17000, 0.12, 0.25)]:
        for lang, value in (("de", de), ("fr", fr)):
            runs.append(
                run(
                    model=f"fondue-step-{step}",
                    provider="melt/fondue",
                    benchmark="voxpopuli-dev",
                    slice=f"asr-voxpopuli-{lang}",
                    task="asr",
                    src=None,
                    tgt=None,
                    lang=lang,
                    metrics={"corpus_wer_raw": value},
                )
            )
    body = build_languages(cfg, runs, "VoxPopuli", "melt/fondue", "corpus_wer_raw", n=1)
    assert body["worst_now"][0]["lang"] == "fr"


def test_latest_is_each_languages_own_last_point(cfg):
    runs = _bleu_runs([(102000, 20.0), (162000, 10.0)], "mt") + _bleu_runs(
        [(102000, 30.0), (162000, 32.0), (270000, 31.0)], "de"
    )
    body = build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu", n=5)
    mt = next(item for item in body["worst_now"] if item["lang"] == "mt")
    assert mt["latest"] == 10.0
    assert mt["latest_step"] == 162000


def test_onset_is_the_start_of_the_final_sustained_drop(cfg):
    runs = _bleu_runs([(102000, 20.0), (108000, 10.0), (162000, 20.0), (270000, 10.0)], "mt")
    body = build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu", threshold=0.1)
    assert body["regressions"][0]["onset"] == 270000


def test_unknown_family_or_metric_returns_none(cfg):
    runs = _bleu_runs([(102000, 20.0), (162000, 10.0)])
    assert build_languages(cfg, runs, "Nope", "smurf/16n", "corpus_bleu") is None
    assert build_languages(cfg, runs, "FLEURS XtoEN", "smurf/16n", "nope") is None


def test_weighted_zero_samples_does_not_crash_languages(cfg):
    weighted = dataclasses.replace(cfg, aggregate="weighted")
    runs = _bleu_runs([(102000, 20.0), (162000, 10.0)], "de")
    for item in runs:
        object.__setattr__(item, "total_samples", 0)
    body = build_languages(weighted, runs, "FLEURS XtoEN", "smurf/16n", "corpus_bleu")
    assert body["points"] == []
    assert body["regressions"] == []


def test_st_trend_default_is_metricx(cfg):
    tasks = build_trends(cfg, [])["tasks"]
    assert tasks["st"]["metric"] == "metricx"
    assert tasks["asr"]["metric"] == "corpus_wer_raw"


def test_undefined_trend_metric_falls_back_to_the_headline(cfg):
    broken = dataclasses.replace(cfg, trend_metrics={"st": "nope"})
    assert build_trends(broken, [])["tasks"]["st"]["metric"] == "corpus_bleu"
