from __future__ import annotations

import dataclasses

from worklog.config import Lineage
from worklog.trends import build_trends, fold_runs, step_of

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
