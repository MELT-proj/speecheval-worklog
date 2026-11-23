def test_index_serves_vendored_assets(client):
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    for needle in ('id="matrix"', 'id="heatmap"', 'id="feed"', "/static/vendor/echarts.min.js"):
        assert needle in html
    for asset in ("echarts.min.js",):
        assert client.get(f"/static/vendor/{asset}").status_code == 200


def test_trends_page_and_nav_are_served(client):
    index = client.get("/").get_data(as_text=True)
    inspect = client.get("/inspect").get_data(as_text=True)
    for html in (index, inspect):
        assert 'class="nav"' in html
        assert 'href="/trends"' in html
    page = client.get("/trends")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    for needle in ('id="trend-panels"', "/static/trends.js", 'id="leaderboard"', 'id="language-pane"'):
        assert needle in body
