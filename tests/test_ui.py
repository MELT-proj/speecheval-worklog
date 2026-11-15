"""Browser tests for the page itself.

Run with a Playwright-capable interpreter (see README, "Development"); they skip
when Playwright is absent. Chromium needs the system libraries Playwright lists
in ``playwright install-deps`` — on artemis they are staged under
/tmp/opencode/pwlibs and LD_LIBRARY_PATH.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
from werkzeug.serving import make_server

from worklog.app import create_app
from worklog.config import load_config
from worklog.scanner import iter_log_paths, scan_run
from worklog.store import Store

from .helpers import make_log, make_sample_log


def _scan(path):
    row = scan_run(Path(path))
    assert row is not None
    return row


@pytest.fixture
def inspector_server(tmp_path):
    path = make_sample_log(tmp_path, samples=30)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    app = create_app(load_config(), store)
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", store.all()[0].row_id
    server.shutdown()


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
    for suffix, wer in (("clean", 0.02), ("other", 0.08)):
        make_log(
            tmp_path,
            benchmark=f"librispeech-validation-{suffix}",
            slice_id=f"asr-librispeech-validation-{suffix}",
            task="asr",
            dataset_id=f"librispeech-validation-{suffix}",
            src=None,
            tgt=None,
            lang="en",
            metrics={"corpus_wer_raw": wer},
        )
    make_log(
        tmp_path,
        model="cohere-transcribe-03-2026",
        eval_model="melt/hf/cohere_transcribe",
        benchmark="voxpopuli-dev",
        slice_id="asr-voxpopuli-en-dev",
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
        page.wait_for_selector("#heatmap canvas")
        assert page.locator("#heatmap-tabs .tab").all_inner_texts() == ["FLEURS XtoEN"]
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["de"]

        page.select_option("#metric-st", "corpus_chrf")
        page.wait_for_function("() => document.querySelector('#matrix').innerText.includes('40.00')")
        assert "metric-st=corpus_chrf" in page.url

        page.select_option("#metric-st", "metricx")
        page.wait_for_function("() => document.querySelector('#matrix').innerText.includes('…')")
        page.screenshot(path=str(tmp_path / "page.png"), full_page=True)

        page.check("#show-all-checkpoints")
        assert "showAll=1" in page.url
        page.uncheck("#show-all-checkpoints")
        assert "showAll=1" not in page.url

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


def test_matrix_drops_old_checkpoints_and_unsupported_tasks(tmp_path):
    for step in (3000, 26000):
        make_log(
            tmp_path,
            campaign="speechval-fondue",
            model=f"fondue-step-{step}",
            eval_model=f"melt/fondue-step-{step}",
            benchmark="fleurs24-st-xen-dev",
            slice_id="st-fleurs-de_en",
            metrics={"corpus_bleu": 20.0 + step / 1000},
        )
    make_log(
        tmp_path,
        model="cohere-transcribe-03-2026",
        eval_model="melt/hf/cohere_transcribe",
        benchmark="voxpopuli-dev",
        slice_id="asr-voxpopuli-en-dev",
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
            snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
            page.evaluate(
                "([snap, state]) => window.Worklog.matrix.render(snap, state)",
                [snapshot, {"aggregate": "macro"}],
            )
            asr = page.locator(".matrix-block").nth(0).inner_text()
            st = page.locator(".matrix-block").nth(1).inner_text()
            assert "cohere-transcribe-03-2026" in asr
            assert "cohere-transcribe-03-2026" not in st
            assert "fondue-step-26000" in st
            assert "fondue-step-3000" not in st

            page.evaluate(
                "([snap, state]) => window.Worklog.matrix.render(snap, state)",
                [snapshot, {"aggregate": "macro", "showAllCheckpoints": True}],
            )
            assert "fondue-step-3000" in page.locator(".matrix-block").nth(1).inner_text()
            browser.close()
    finally:
        server.shutdown()


def test_heatmap_has_tabs_and_variant_columns(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(live_server)
        page.wait_for_selector("#heatmap canvas")

        tabs = page.locator("#heatmap-tabs .tab").all_inner_texts()
        assert tabs == ["LibriSpeech", "VoxPopuli", "FLEURS XtoEN"]

        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["clean", "other"]
        assert option["yAxis"][0]["data"] == ["canary-1b-v2"]
        assert len(option["series"][0]["data"]) == 2  # one value per benchmark, not overlaid

        page.select_option("#filter-language", "en")
        page.click("#heatmap-tabs .tab >> text=VoxPopuli")
        page.wait_for_function(
            "() => document.getElementById('heatmap-hint').textContent.includes('VoxPopuli')"
        )
        assert "heatmap=VoxPopuli" in page.url
        assert "language=en" in page.url
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["en"]
        assert len(option["series"][0]["data"]) == 2  # canary and cohere

        page.click("#heatmap-tabs .tab >> text=FLEURS XtoEN")
        page.wait_for_function(
            "() => document.getElementById('heatmap-hint').textContent.includes('FLEURS XtoEN')"
        )
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["yAxis"][0]["data"] == ["canary-1b-v2"]  # cohere has no ST
        browser.close()


def test_latest_checkpoint_is_scoped_to_the_selected_campaign(tmp_path):
    make_log(
        tmp_path,
        campaign="speechval-old",
        model="seed-step-10",
        eval_model="melt/seed-step-10",
        benchmark="fleurs24-st-xen-dev",
        slice_id="st-fleurs-de_en",
        metrics={"corpus_bleu": 20.0},
    )
    make_log(
        tmp_path,
        campaign="speechval-new",
        model="seed-step-20",
        eval_model="melt/seed-step-20",
        benchmark="fleurs24-st-xen-dev",
        slice_id="st-fleurs-de_en",
        metrics={"corpus_bleu": 30.0},
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
            snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
            page.evaluate(
                "([snap, state]) => window.Worklog.matrix.render(snap, state)",
                [snapshot, {"aggregate": "macro", "campaign": "speechval-old"}],
            )
            assert "seed-step-10" in page.locator(".matrix-block").nth(1).inner_text()

            page.evaluate(
                "([snap, state]) => window.Worklog.matrix.render(snap, state)",
                [snapshot, {"aggregate": "macro"}],
            )
            st = page.locator(".matrix-block").nth(1).inner_text()
            assert "seed-step-20" in st
            assert "seed-step-10" not in st
            browser.close()
    finally:
        server.shutdown()


def test_heatmap_points_to_show_all_when_only_hidden_checkpoints_ran(tmp_path):
    make_log(
        tmp_path,
        campaign="speechval-fondue",
        model="fondue-step-3000",
        eval_model="melt/fondue-step-3000",
        benchmark="fleurs24-st-xen-dev",
        slice_id="st-fleurs-de_en",
        metrics={"corpus_bleu": 20.0},
    )
    make_log(
        tmp_path,
        campaign="speechval-fondue",
        model="fondue-step-26000",
        eval_model="melt/fondue-step-26000",
        benchmark="fleurs24-st-enx-dev",
        slice_id="st-fleurs-en_de",
        src="en",
        tgt="de",
        dataset_id="fleurs-en_de",
        metrics={"corpus_bleu": 30.0},
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
            page.wait_for_function(
                "() => document.getElementById('heatmap').innerText.includes('Show all checkpoints')"
            )
            assert page.locator("#heatmap canvas").count() == 0
            browser.close()
    finally:
        server.shutdown()


def test_inspector_renders_setup_and_samples(inspector_server, tmp_path):
    base, rid = inspector_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        page.goto(f"{base}/inspect?run={rid}")
        page.wait_for_selector("#samples .sample")
        assert "canary-1b-v2" in page.inner_text("#run-title")
        assert "smurf" in page.inner_text("#setup")
        assert "the generated 0" in page.inner_text("#samples")
        assert "1–25 of 30" in page.inner_text("#sample-count")
        page.fill("#sample-search", "reference 2.")
        page.wait_for_function("() => document.querySelector('#sample-count').textContent.includes('1–1 of 1')")
        assert "03-000002" in page.inner_text("#samples")
        page.fill("#sample-search", "")
        page.wait_for_function("() => document.querySelector('#sample-count').textContent.includes('1–25 of 30')")
        page.click("#sample-next")
        page.wait_for_function("() => document.querySelector('#samples').textContent.includes('03-000025')")
        page.screenshot(path=str(tmp_path / "inspector.png"), full_page=True)
        browser.close()
