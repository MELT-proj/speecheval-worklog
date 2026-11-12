"""Browser tests for the page itself.

Run with a Playwright-capable interpreter (see README, "Development"); they skip
when Playwright is absent. Chromium needs the system libraries Playwright lists
in ``playwright install-deps`` — on artemis they are staged under
/tmp/opencode/pwlibs and LD_LIBRARY_PATH.
"""

from __future__ import annotations

import threading

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
from werkzeug.serving import make_server

from worklog.app import create_app
from worklog.config import load_config
from worklog.scanner import iter_log_paths, scan_run
from worklog.store import Store

from .helpers import make_log


@pytest.fixture
def live_server(tmp_path):
    scorer = ("melteval/rescore.py@metricx_scorer",)
    make_log(
        tmp_path,
        slice_id="st-fleurs-de_en",
        src="de",
        tgt="en",
        metrics={"corpus_bleu": 20.0, "corpus_chrf": 40.0},
        post_scorers=scorer,
    )
    make_log(
        tmp_path,
        slice_id="st-fleurs-fr_en",
        src="fr",
        tgt="en",
        metrics={"corpus_bleu": 30.0, "corpus_chrf": 60.0},
        created="2026-11-12T11:00:00+00:00",
        post_scorers=scorer,
    )
    make_log(
        tmp_path,
        benchmark="something-new",
        slice_id="st-mystery-en",
        src="xx",
        tgt="en",
        metrics={"corpus_bleu": 1.0},
        created="2026-11-12T12:00:00+00:00",
    )
    make_log(
        tmp_path,
        benchmark="voxpopuli-dev",
        slice_id="asr-en",
        task="asr",
        dataset_id="voxpopuli-en-dev",
        src=None,
        tgt=None,
        lang="en",
        metrics={"corpus_wer_raw": 0.1},
    )
    cfg = load_config(overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")})
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_matrix_renders_and_drilldown_opens(live_server, tmp_path):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(live_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro"}],
        )
        page.wait_for_selector(".matrix-table tbody tr")
        text = page.inner_text("#matrix")
        assert "FLEURS XTOEN" in text.upper()
        assert "25.00" in text  # (20 + 30) / 2, both languages
        assert "10.00" in text  # WER 0.1 rendered as a percentage
        assert "BASELINES" in text.upper()  # grouped by provider, not display name
        page.screenshot(path=str(tmp_path / "matrix.png"), full_page=True)
        page.locator(".cell:not(.empty)").first.click()
        page.wait_for_selector("#modal[open]")
        assert page.inner_text("#modal-title")
        page.screenshot(path=str(tmp_path / "drilldown.png"), full_page=True)
        browser.close()


def test_page_boots_with_kpis_heatmap_feed_and_filters(live_server, tmp_path):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1400})
        page.goto(live_server)
        page.wait_for_selector("#kpis .kpi")
        page.wait_for_selector("#heatmap canvas")
        page.wait_for_selector("#feed .run")

        assert "MODELS" in page.inner_text("#kpis").upper()
        assert "success" in page.inner_text("#feed").lower()
        assert "updated" in page.inner_text("#statusline")
        assert page.locator("#filter-language").count() == 1
        page.screenshot(path=str(tmp_path / "page-default.png"), full_page=True)

        assert "UNCLASSIFIED" in page.inner_text("#matrix").upper()

        page.select_option("#filter-language", "de")
        page.wait_for_function("() => document.querySelector('#matrix').innerText.includes('20.00')")
        assert "language=de" in page.url

        page.select_option("#metric-st", "corpus_chrf")
        page.wait_for_function("() => document.querySelector('#matrix').innerText.includes('40.00')")
        assert "metric-st=corpus_chrf" in page.url

        page.select_option("#metric-st", "metricx")
        page.wait_for_function("() => document.querySelector('#matrix').innerText.includes('…')")
        page.screenshot(path=str(tmp_path / "page.png"), full_page=True)

        page.click("#refresh")
        page.wait_for_function("() => document.getElementById('refresh').textContent === 'Refresh'")
        browser.close()


def test_asr_only_campaign_gets_a_heatmap(tmp_path):
    make_log(
        tmp_path,
        benchmark="voxpopuli-dev",
        slice_id="asr-en",
        task="asr",
        dataset_id="voxpopuli-en-dev",
        src=None,
        tgt=None,
        lang="en",
        metrics={"corpus_wer_raw": 0.1},
    )
    cfg = load_config(overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")})
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1500, "height": 1200})
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_selector("#heatmap canvas")
            assert "WER" in page.inner_text("#heatmap-hint")
            browser.close()
    finally:
        server.shutdown()


def test_theme_toggle_switches_and_persists(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(live_server)
        page.wait_for_selector("#theme-toggle")
        initial = page.evaluate("document.documentElement.dataset.theme")
        other = "dark" if initial == "light" else "light"
        before = page.evaluate("getComputedStyle(document.querySelector('.kpi')).backgroundColor")

        page.click("#theme-toggle")
        page.wait_for_function(f"() => document.documentElement.dataset.theme === '{other}'")
        after = page.evaluate("getComputedStyle(document.querySelector('.kpi')).backgroundColor")
        assert after != before

        page.reload()
        page.wait_for_selector("#theme-toggle")
        assert page.evaluate("document.documentElement.dataset.theme") == other

        page.click("#theme-toggle")
        page.wait_for_function(f"() => document.documentElement.dataset.theme === '{initial}'")
        browser.close()
