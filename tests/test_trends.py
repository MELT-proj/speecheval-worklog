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
