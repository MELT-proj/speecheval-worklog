from worklog.views import aggregate, build_snapshot, family_of

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
