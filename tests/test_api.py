from __future__ import annotations

import dataclasses

import pytest

from .helpers import make_sample_log, run


@pytest.fixture
def isolated_client(cfg, store, tmp_path):
    """A client whose digest sidecars land under ``tmp_path``, not the shared cache dir."""
    from worklog.app import create_app

    client_cfg = dataclasses.replace(cfg, cache=tmp_path / "w.sqlite")
    return create_app(client_cfg, store).test_client()


def test_snapshot_shape(client, store):
    store.sync([run("a.json", metrics={"corpus_bleu": 24.5})])
    r = client.get("/api/snapshot")
    body = r.get_json()
    assert r.status_code == 200
    assert body["version"] and body["slices"][0]["metrics"]["corpus_bleu"] == 24.5
    assert set(body) == {
        "version",
        "generated_at",
        "aggregate",
        "kpis",
        "filters",
        "model_families",
        "checkpoints",
        "slices",
        "matrix",
        "runs",
    }


def test_version_match_is_204(client, store):
    store.sync([run("a.json")])
    v = client.get("/api/snapshot").get_json()["version"]
    assert client.get(f"/api/snapshot?version={v}").status_code == 204


def test_refresh_rescans_and_bumps_version(client, store, tmp_path):
    store.sync([run("a.json")])
    v = client.get("/api/snapshot").get_json()["version"]
    assert client.post("/api/refresh").status_code == 200
    assert client.get(f"/api/snapshot?version={v}").status_code in (204, 200)


def test_run_detail_and_missing(client, store):
    store.sync([run("a.json")])
    rid = store.all()[0].row_id
    assert client.get(f"/api/run/{rid}").status_code == 200
    assert client.get("/api/run/9999").status_code == 404
    assert client.get("/api/run/9999").get_json()["error"]


def test_health(client):
    assert client.get("/api/health").get_json()["ok"] is True


def test_refresh_invokes_the_scan_callback(cfg, store):
    from worklog.app import create_app

    calls = []
    client = create_app(cfg, store, scan=lambda: calls.append(1)).test_client()
    assert client.post("/api/refresh").status_code == 200
    assert calls == [1]


def test_overview_returns_setup(isolated_client, store, tmp_path):
    path = make_sample_log(tmp_path, samples=2)
    store.sync([run(str(path), model="canary-1b-v2", task="asr")])
    rid = store.all()[0].row_id
    body = isolated_client.get(f"/api/run/{rid}/overview").get_json()
    assert body["run"]["model"] == "canary-1b-v2"
    assert body["setup"]["task_args"]["prompt_style"] == "smurf"
    assert body["setup"]["generate_config"] == {"max_tokens": 256}
    assert body["digest"] == {"samples": 2, "failed": 0, "flagged": 0,
                              "sorts": ["order", "worst", "slowest", "longest"]}


def test_samples_endpoint_pages_filters_and_sorts(isolated_client, store, tmp_path):
    path = make_sample_log(tmp_path, samples=30)
    store.sync([run(str(path), task="asr")])
    rid = store.all()[0].row_id
    page = isolated_client.get(f"/api/run/{rid}/samples?offset=10&limit=5").get_json()
    assert page["total"] == 30 and page["offset"] == 10 and len(page["samples"]) == 5
    assert page["samples"][0]["id"] == "03-000010"
    narrow = isolated_client.get(f"/api/run/{rid}/samples?q=reference%202.&limit=100").get_json()
    assert [r["id"] for r in narrow["samples"]] == ["03-000002"]
    worst = isolated_client.get(f"/api/run/{rid}/samples?sort=worst&limit=3").get_json()
    # wer is i/10 over samples 0..29, so the worst three are 29, 28, 27.
    # (The plan said [0.9, 0.8, 0.7], which only fits a 10-sample log.)
    assert [r["wer"] for r in worst["samples"]] == [2.9, 2.8, 2.7]
    capped = isolated_client.get(f"/api/run/{rid}/samples?limit=5000").get_json()
    assert capped["limit"] == 100


def test_inspect_page_is_served(client):
    response = client.get("/inspect")
    assert response.status_code == 200
    assert b"SpeechEval" in response.data


def test_inspector_errors_are_typed(isolated_client, store, tmp_path):
    store.sync([run(str(tmp_path / "gone.json"), task="asr")])
    rid = store.all()[0].row_id
    assert isolated_client.get(f"/api/run/{rid}/overview").status_code == 404
    assert isolated_client.get(f"/api/run/{rid}/samples").status_code == 404
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    store.sync([run(str(broken), task="asr")])
    broken_id = next(r.row_id for r in store.all() if r.path == str(broken))
    response = isolated_client.get(f"/api/run/{broken_id}/overview")
    assert response.status_code == 409 and response.get_json()["error"]
    assert isolated_client.get("/api/run/9999/overview").status_code == 404


def test_trends_payload_shape(client, store):
    store.sync([
        run("a.json", model="fondue-step-3000", provider="melt/fondue-step-3000",
            metrics={"corpus_bleu": 20.0}),
    ])
    body = client.get("/api/trends").get_json()
    assert set(body) == {
        "version", "generated_at", "aggregate", "lineages", "tasks", "panels", "checkpoints",
    }
    assert body["lineages"][0]["id"] == "melt/fondue"
    assert body["panels"][0]["family"] == "FLEURS XtoEN"
    assert body["panels"][0]["points"][0]["values"]["corpus_bleu"] == 20.0


def test_trends_version_match_is_204(client, store):
    store.sync([run()])
    version = client.get("/api/trends").get_json()["version"]
    assert client.get(f"/api/trends?version={version}").status_code == 204
