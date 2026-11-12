def test_index_serves_vendored_assets(client):
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    for needle in ('id="matrix"', 'id="heatmap"', 'id="feed"', "/static/vendor/echarts.min.js"):
        assert needle in html
    for asset in ("echarts.min.js",):
        assert client.get(f"/static/vendor/{asset}").status_code == 200
