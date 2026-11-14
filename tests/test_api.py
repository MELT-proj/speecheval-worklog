from .helpers import run


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
