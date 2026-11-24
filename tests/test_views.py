from worklog.views import aggregate, build_snapshot, family_of, match_family

from .helpers import run


def test_europarl_split_by_direction(cfg):
    fam = family_of(cfg, run(benchmark="europarl-st-dev", task="st", src="fr", tgt="en"))
    assert fam.name == "Europarl XtoEN"
    fam = family_of(cfg, run(benchmark="europarl-st-dev", task="st", src="en", tgt="de"))
    assert fam.name == "Europarl ENtoX"


def test_unknown_benchmark_is_unclassified(cfg):
    assert family_of(cfg, run(benchmark="something-new")) is None


def test_macro_and_weighted():
    assert aggregate([(10.0, 1), (20.0, 3)], "macro") == 15.0
    assert aggregate([(10.0, 1), (20.0, 3)], "weighted") == 17.5
    assert aggregate([], "macro") is None


def test_matrix_cell_states(cfg):
    runs = [
        run(benchmark="fleurs24-st-xen-dev", slice="st-fleurs-de_en", src="de", tgt="en",
            metrics={"corpus_bleu": 20.0}),
        run(benchmark="fleurs24-st-xen-dev", slice="st-fleurs-fr_en", src="fr", tgt="en",
            metrics={"corpus_bleu": 30.0}, pending=("metricx_scorer",)),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    cell = next(c for c in snap["matrix"]["st"]["cells"] if c["family"] == "FLEURS XtoEN")
    assert cell["value"] == 25.0 and cell["n"] == 2 and cell["total"] == 2
    assert snap["kpis"]["pending"] == 1


def test_pending_only_cell(cfg):
    runs = [run(metrics={}, pending=("metricx_scorer",))]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    cell = next(c for c in snap["matrix"]["st"]["cells"] if c["family"] == "FLEURS XtoEN")
    assert cell["status"] == "pending" and cell["value"] is None


def test_explicit_failed_count_overrides_feed(cfg):
    snap = build_snapshot(cfg, [], [], "v1", "now", failed=9)
    assert snap["kpis"]["failed"] == 9


def test_snapshot_exposes_aggregate_and_metric_choices(cfg):
    snap = build_snapshot(cfg, [run()], [], "v1", "now")
    assert snap["aggregate"] == cfg.aggregate
    names = {m["name"] for m in snap["matrix"]["st"]["metrics"]}
    assert {"corpus_bleu", "corpus_chrf", "metricx"} <= names
    assert snap["matrix"]["st"]["metric"] == "corpus_bleu"


def test_warnings_are_passed_through(cfg):
    snap = build_snapshot(cfg, [], [], "v1", "now", warnings=4)
    assert snap["kpis"]["warnings"] == 4


def test_highest_step_is_the_latest_checkpoint(cfg):
    runs = [
        run(model="fondue-step-3000", provider="melt/fondue-step-3000"),
        run(model="fondue-step-26000", provider="melt/fondue-step-26000"),
        run(model="canary-1b-v2", provider="melt/hf/canary"),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    checkpoints = snap["checkpoints"]
    assert checkpoints["fondue-step-26000"] == {"base": "fondue", "step": 26000, "latest": True}
    assert checkpoints["fondue-step-3000"]["latest"] is False
    assert checkpoints["canary-1b-v2"] == {"base": "canary-1b-v2", "step": None, "latest": True}


def test_slice_rows_carry_the_run_id(cfg):
    snap = build_snapshot(cfg, [run(row_id=42)], [], "v1", "now")
    assert snap["slices"][0]["run_id"] == 42


def test_latest_is_computed_within_a_model_family(cfg):
    runs = [
        run(model="seed-step-5", provider="melt/seed-step-5"),
        run(model="seed-step-9", provider="smurf/seed-step-9"),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    assert snap["checkpoints"]["seed-step-5"]["latest"] is True
    assert snap["checkpoints"]["seed-step-9"]["latest"] is True


def test_models_are_ordered_by_numeric_step_ascending(cfg):
    runs = [
        run(model="smurf-b-step-20", provider="smurf/x"),
        run(model="smurf-b-step-3", provider="smurf/x"),
        run(model="fondue-step-26000", provider="melt/fondue-step-26000"),
        run(model="fondue-step-3000", provider="melt/fondue-step-3000"),
        run(model="canary-1b-v2", provider="melt/hf/canary"),
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    assert snap["filters"]["models"] == [
        "canary-1b-v2",
        "fondue-step-3000",
        "fondue-step-26000",
        "smurf-b-step-3",
        "smurf-b-step-20",
    ]


def test_polyspeech_audio_mcq_is_a_classified_family(cfg):
    runs = [
        run(
            benchmark="polyspeech-100-pa_qt-200",
            slice="audio_mcq-polyspeech-pa_qt-de",
            task="audio_mcq",
            src=None,
            tgt=None,
            lang="de",
            metrics={"choice_accuracy": 0.465, "unresolved_rate": 0.0},
        )
    ]
    snap = build_snapshot(cfg, runs, [], "v1", "now")
    assert snap["slices"][0]["family"] == "PolySpeech"
    assert snap["matrix"]["audio_mcq"]["metric"] == "choice_accuracy"
    cell = next(c for c in snap["matrix"]["audio_mcq"]["cells"] if c["family"] == "PolySpeech")
    assert cell["value"] == 0.465


def test_match_family_per_split(cfg):
    assert match_family(cfg.families, "covost2-test", "st", "xen", split="test").name == "CoVoST2 XtoEN"
    assert match_family(cfg.families, "covost2-test", "st", "xen", split="validation") is None
    assert match_family(cfg.families, "covost2-dev", "st", "xen", split="validation").name == "CoVoST2 XtoEN"
    assert match_family(cfg.families, "covost2-dev", "st", "xen", split="test") is None


def test_a_matrix_family_matches_only_its_own_split(cfg):
    test_run = run(
        model="fondue-step-1000", benchmark="covost2-test", slice="st-covost2-de_en",
        metrics={"corpus_bleu": 20.0},
    )  # fmt: skip
    payload = build_snapshot(cfg, [test_run], [], "v", "now", split="test")
    families = {cell["family"] for cell in payload["matrix"]["st"]["cells"]}
    assert "CoVoST2 XtoEN" in families
    assert payload["slices"][0]["family"] == "CoVoST2 XtoEN"
    payload = build_snapshot(cfg, [test_run], [], "v", "now")
    assert payload["slices"][0]["family"] == "unclassified"


def test_empty_test_family_keeps_its_column(cfg):
    speech = run(model="fondue-step-1000", benchmark="librispeech-test-clean", slice="asr-librispeech-test-clean",
                 task="asr", src=None, tgt=None, lang="en", metrics={"corpus_wer_raw": 0.1})  # fmt: skip
    payload = build_snapshot(cfg, [speech], [], "v", "now", split="test")
    assert "PolySpeech" in payload["filters"]["families"]
    cells = payload["matrix"]["audio_mcq"]["cells"]
    assert [cell["status"] for cell in cells if cell["family"] == "PolySpeech"] == ["empty"]


def test_an_unknown_benchmark_is_unclassified_in_both_splits(cfg):
    # Whether an unknown benchmark lands on the test page is the app's input
    # filter (see the API tests); here it must simply match no family in either split.
    stranger = run(benchmark="something-new")
    assert build_snapshot(cfg, [stranger], [], "v", "now")["slices"][0]["family"] == "unclassified"
    assert build_snapshot(cfg, [stranger], [], "v", "now", split="test")["slices"][0]["family"] == "unclassified"
