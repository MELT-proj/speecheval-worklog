"""Browser tests for the page itself.

Run with a Playwright-capable interpreter (see README, "Development"); they skip
when Playwright is absent. Chromium needs the system libraries Playwright lists
in ``playwright install-deps`` — on artemis they are staged under
/tmp/opencode/pwlibs and LD_LIBRARY_PATH.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import pytest

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
from werkzeug.serving import make_server

from worklog.app import create_app
from worklog.config import load_config
from worklog.scanner import iter_log_paths, scan_run
from worklog.store import Store

from .helpers import make_log, make_mcq_log, make_sample_log


def _scan(path):
    row = scan_run(Path(path))
    assert row is not None
    return row


@pytest.fixture
def inspector_server(tmp_path):
    path = make_sample_log(
        tmp_path,
        samples=30,
        extra_metrics={"details": {"name": "details", "value": {"wer_errors": 5, "ref_words": 10}}},
    )
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    cfg = dataclasses.replace(load_config(), cache=tmp_path / "w.sqlite")
    app = create_app(cfg, store)
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
        assert "batch_size" in page.inner_text("#setup")
        assert "[object Object]" not in page.inner_text("#setup")
        assert "[object Object]" not in page.inner_text("#scores")
        assert "wer_errors" in page.inner_text("#scores")
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


def test_drilldown_and_feed_link_to_the_inspector(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(live_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate("([snap, state]) => window.Worklog.matrix.render(snap, state)",
                      [snapshot, {"aggregate": "macro"}])
        page.locator(".cell:not(.empty)").first.click()
        page.wait_for_selector("#modal[open]")
        href = page.get_attribute("#modal a.samples", "href")
        assert href.startswith("/inspect?run=") and "back=" in href
        page.click("#modal-close")
        page.wait_for_selector("#feed a.run")
        feed_href = page.get_attribute("#feed a.run", "href")
        assert feed_href.startswith("/inspect?run=") and "back=" in feed_href
        browser.close()


@pytest.fixture
def locked_server(tmp_path):
    cfg = load_config(overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")})
    store = Store(cfg.cache)
    app = create_app(cfg, store, password="letmein")
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_login_shields_the_dashboard(locked_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(locked_server)
        page.wait_for_selector("#login-password")
        assert "/login" in page.url

        page.fill("#login-password", "wrong")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#login-error:not([hidden])")
        assert "not right" in page.inner_text("#login-error")

        page.fill("#login-password", "letmein")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#kpis .kpi")
        assert page.evaluate("() => fetch('/api/snapshot').then((r) => r.status)") == 200

        context.clear_cookies()
        page.click("#refresh")
        page.wait_for_selector("#login-password")

        page.fill("#login-password", "letmein")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#refresh")
        page.click("form[action='/logout'] button")
        page.wait_for_selector("#login-password")
        assert page.evaluate("() => fetch('/api/snapshot').then((r) => r.status)") == 401
        browser.close()


@pytest.fixture
def locked_inspector_server(tmp_path):
    path = make_sample_log(tmp_path, samples=3)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    cfg = dataclasses.replace(load_config(), cache=tmp_path / "w.sqlite")
    app = create_app(cfg, store, password="letmein")
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", store.all()[0].row_id
    server.shutdown()


def test_inspector_sends_an_expired_session_to_login(locked_inspector_server):
    base, rid = locked_inspector_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{base}/inspect?run={rid}")
        page.wait_for_selector("#login-password")
        page.fill("#login-password", "letmein")
        page.click("form.login-card button[type=submit]")
        page.wait_for_selector("#samples .sample")

        context.clear_cookies()
        page.fill("#sample-search", "reference 1.")
        page.wait_for_selector("#login-password")
        browser.close()


def test_column_helpers_are_shared(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(live_server)
        page.wait_for_function("() => !!(window.Worklog && typeof window.Worklog.axisFor === 'function')")
        assert page.evaluate(
            """() => window.Worklog.axisFor([
                 {benchmark: 'librispeech-validation-clean', lang: 'en'},
                 {benchmark: 'librispeech-validation-other', lang: 'en'},
               ])"""
        ) == {
            "mode": "benchmark",
            "values": ["librispeech-validation-clean", "librispeech-validation-other"],
        }
        assert page.evaluate(
            """() => window.Worklog.axisFor([
                 {benchmark: 'voxpopuli-dev', lang: 'de'},
                 {benchmark: 'voxpopuli-dev', lang: 'en'},
               ])"""
        ) == {"mode": "language", "values": ["de", "en"]}
        assert page.evaluate(
            """() => {
                 const W = window.Worklog;
                 const row = {lang: null, direction: 'xen', src: 'de', tgt: 'en'};
                 return [W.languageOf(row), W.columnOf(row, 'language')];
               }"""
        ) == ["de", "de"]
        browser.close()


def test_common_columns_intersects_participating_models(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(live_server)
        page.wait_for_function("() => !!(window.Worklog && window.Worklog.commonColumns)")
        result = page.evaluate(
            """() => {
                 const W = window.Worklog;
                 const slice = (model, lang, wer) => ({
                   model, task: 'asr', family: 'VoxPopuli', benchmark: 'voxpopuli-dev',
                   lang, src: null, tgt: null, samples: 10,
                   metrics: wer === null ? {} : {corpus_wer_raw: wer},
                 });
                 const snapshot = {
                   slices: [
                     slice('a', 'en', 0.1), slice('a', 'de', 0.2), slice('a', 'fr', 0.3),
                     slice('b', 'en', 0.1), slice('b', 'de', 0.2),
                     {model: 'c', task: 'asr', family: 'LibriSpeech', benchmark: 'librispeech-validation-clean',
                      lang: 'en', src: null, tgt: null, samples: 10, metrics: {corpus_wer_raw: 0.05}},
                   ],
                   filters: {models: ['a', 'b', 'c'], families: ['LibriSpeech', 'VoxPopuli']},
                   model_families: {a: 'baseline', b: 'baseline', c: 'baseline'},
                   checkpoints: {},
                 };
                 const common = W.commonColumns(snapshot, {aggregate: 'macro'}, 'asr', 'corpus_wer_raw');
                 const entry = common.get('VoxPopuli');
                 return {mode: entry.mode, columns: [...entry.columns].sort(),
                         kept: snapshot.slices.filter((row) => W.keepsRow(common, row)).length};
               }"""
        )
        assert result == {"mode": "language", "columns": ["de", "en"], "kept": 5}
        browser.close()


@pytest.fixture
def coverage_server(tmp_path):
    asr = (("en", 0.10, "10:00"), ("de", 0.30, "10:10"), ("fr", 0.30, "10:20"))
    for lang, wer, at in asr:
        make_log(
            tmp_path,
            campaign="speechval-coverage",
            benchmark="voxpopuli-dev",
            slice_id=f"asr-{lang}",
            task="asr",
            dataset_id=f"voxpopuli-{lang}-dev",
            src=None,
            tgt=None,
            lang=lang,
            metrics={"corpus_wer_raw": wer},
            created=f"2026-11-12T{at}:00+00:00",
        )
    for lang, bleu, at in (("de", 20.0, "11:00"), ("fr", 30.0, "11:10")):
        make_log(
            tmp_path,
            campaign="speechval-coverage",
            benchmark="fleurs24-st-xen-dev",
            slice_id=f"st-fleurs-{lang}_en",
            dataset_id=f"fleurs-{lang}_en",
            src=lang,
            tgt="en",
            metrics={"corpus_bleu": bleu},
            created=f"2026-11-12T{at}:00+00:00",
        )
    for lang, wer, at in (("en", 0.05, "12:00"), ("de", 0.15, "12:10")):
        make_log(
            tmp_path,
            campaign="speechval-coverage",
            model="cohere-transcribe-03-2026",
            eval_model="melt/hf/cohere_transcribe",
            benchmark="voxpopuli-dev",
            slice_id=f"asr-{lang}",
            task="asr",
            dataset_id=f"voxpopuli-{lang}-dev",
            src=None,
            tgt=None,
            lang=lang,
            metrics={"corpus_wer_raw": wer},
            created=f"2026-11-12T{at}:00+00:00",
        )
    make_log(
        tmp_path,
        campaign="speechval-coverage",
        model="empty-baseline",
        eval_model="melt/hf/empty",
        benchmark="fleurs24-st-xen-dev",
        slice_id="st-fleurs-it_en",
        dataset_id="fleurs-it_en",
        src="it",
        tgt="en",
        metrics={},
        created="2026-11-12T13:00:00+00:00",
    )
    cfg = load_config(
        overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")}
    )
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_common_languages_shrinks_matrix_cells(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro"}],
        )
        asr = page.locator(".matrix-block").nth(0)
        st_before = page.locator(".matrix-block").nth(1).inner_text()
        assert "23.33" in asr.inner_text()  # mean of 0.10, 0.30, 0.30
        assert "3/3" in asr.inner_text()
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "common": {"asr": True}}],
        )
        asr = page.locator(".matrix-block").nth(0)
        assert "20.00" in asr.inner_text()  # en + de only
        assert "2/2" in asr.inner_text()
        assert "23.33" not in asr.inner_text()
        assert page.locator(".matrix-block").nth(1).inner_text() == st_before
        browser.close()


def test_common_languages_toggle_persists_and_resets(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_selector("#common-asr")
        page.check("#common-asr")
        page.wait_for_function(
            "() => document.getElementById('matrix').innerText.includes('20.00')"
        )
        assert "common-asr=1" in page.url
        page.reload()
        page.wait_for_selector("#common-asr")
        assert page.is_checked("#common-asr")
        page.click("#filters >> text=Reset")
        page.wait_for_function("() => !location.hash.includes('common-asr')")
        assert not page.is_checked("#common-asr")
        assert "23.33" in page.locator(".matrix-block").nth(0).inner_text()
        browser.close()


def test_common_languages_empty_state_keeps_the_control(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "common": {"st": True}}],
        )
        st = page.locator(".matrix-block").nth(1)
        assert "every visible model" in st.inner_text()
        assert page.locator("#common-st").is_checked()
        browser.close()


def test_common_languages_shrinks_heatmap(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.charts")
        tabs = page.locator("#heatmap-tabs .tab").all_inner_texts()
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [snapshot, {"aggregate": "macro", "heatmap": "VoxPopuli"}],
        )
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["de", "en", "fr"]
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [snapshot, {"aggregate": "macro", "heatmap": "VoxPopuli", "common": {"asr": True}}],
        )
        option = page.evaluate(
            "() => echarts.getInstanceByDom(document.getElementById('heatmap')).getOption()"
        )
        assert option["xAxis"][0]["data"] == ["de", "en"]
        assert "common only" in page.inner_text("#heatmap-hint")
        assert tabs == page.locator("#heatmap-tabs .tab").all_inner_texts()
        browser.close()


def test_common_languages_heatmap_explains_an_empty_axis(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.charts")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [snapshot, {"aggregate": "macro", "heatmap": "FLEURS XtoEN", "common": {"st": True}}],
        )
        assert "every visible model" in page.inner_text("#heatmap")
        assert page.locator("#heatmap canvas").count() == 0
        browser.close()


def test_common_languages_drilldown_reports_the_shrink(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            """([snap, state]) => window.Worklog.matrix.drill(
                 snap, state, 'canary-1b-v2', 'VoxPopuli', 'asr', 'corpus_wer_raw')""",
            [snapshot, {"aggregate": "macro", "common": {"asr": True}}],
        )
        page.wait_for_selector("#modal[open]")
        body = page.inner_text("#modal-body")
        assert "2 of 3 languages" in body
        assert "common only" in body
        assert page.locator("#modal-body tbody tr").count() == 2
        browser.close()


def test_common_languages_language_filter_still_vetoes(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "language": "fr", "common": {"asr": True}}],
        )
        asr = page.locator(".matrix-block").nth(0)
        assert "every visible model" in asr.inner_text()
        page.evaluate(
            "([snap, state]) => window.Worklog.charts.render(snap, state)",
            [
                snapshot,
                {
                    "aggregate": "macro",
                    "language": "fr",
                    "heatmap": "VoxPopuli",
                    "common": {"asr": True},
                },
            ],
        )
        assert "every visible model" in page.inner_text("#heatmap")
        browser.close()


def test_common_languages_drilldown_keeps_the_plain_header_when_unticked(coverage_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(coverage_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            """([snap, state]) => window.Worklog.matrix.drill(
                 snap, state, 'canary-1b-v2', 'VoxPopuli', 'asr', 'corpus_wer_raw')""",
            [snapshot, {"aggregate": "macro"}],
        )
        page.wait_for_selector("#modal[open]")
        body = page.inner_text("#modal-body")
        assert "3 slice(s)" in body
        assert "common only" not in body
        assert page.locator("#modal-body tbody tr").count() == 3
        browser.close()


@pytest.fixture
def mcqa_server(tmp_path):
    make_mcq_log(
        tmp_path,
        model="eurollm-speech-16n-step-0600000",
        eval_model="smurf//checkpoints/step=600000.ckpt",
        lang="de",
        correct=1,
        resolved=1,
    )
    cfg = load_config(
        overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")}
    )
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_audio_mcq_table_renders_accuracy_as_a_percentage(mcqa_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(mcqa_server)
        page.wait_for_function("window.Worklog && window.Worklog.matrix")
        snapshot = page.evaluate("() => fetch('/api/snapshot').then((r) => r.json())")
        page.evaluate(
            "([snap, state]) => window.Worklog.matrix.render(snap, state)",
            [snapshot, {"aggregate": "macro", "showAllCheckpoints": True}],
        )
        text = page.inner_text("#matrix")
        assert "MULTIPLE-CHOICE QA" in text.upper()
        assert "46.50" in text
        assert "UNCLASSIFIED" not in text.upper()
        assert snapshot["slices"][0]["family"] == "PolySpeech"
        assert snapshot["slices"][0]["lang"] == "de"
        browser.close()


@pytest.fixture
def mcq_inspector_server(tmp_path):
    path = make_mcq_log(tmp_path, samples=1, correct=0, resolved=1)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    cfg = dataclasses.replace(load_config(), cache=tmp_path / "w.sqlite")
    app = create_app(cfg, store)
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", store.all()[0].row_id
    server.shutdown()


def test_inspector_shows_mcq_outcome_instead_of_wer(mcq_inspector_server):
    base, rid = mcq_inspector_server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(f"{base}/inspect?run={rid}")
        page.wait_for_selector("#samples .sample")
        outcome = page.inner_text("#samples .sample-outcome")
        assert "incorrect" in outcome.lower()
        assert page.locator("#samples .sample-wer").count() == 0
        browser.close()


def test_inspector_calls_an_unresolved_mcq_sample_unresolved(tmp_path):
    path = make_mcq_log(tmp_path, samples=1, correct=0, resolved=0)
    store = Store(tmp_path / "w.sqlite")
    store.sync([_scan(path)])
    cfg = dataclasses.replace(load_config(), cache=tmp_path / "w.sqlite")
    app = create_app(cfg, store)
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(f"http://127.0.0.1:{server.server_port}/inspect?run={store.all()[0].row_id}")
            page.wait_for_selector("#samples .sample")
            assert "unresolved" in page.inner_text("#samples .sample-outcome").lower()
            browser.close()
    finally:
        server.shutdown()


@pytest.fixture
def mcqa_heatmap_server(tmp_path):
    for lang, accuracy in (("de", 0.4), ("fr", 0.5)):
        make_mcq_log(tmp_path, lang=lang, accuracy=accuracy)
    cfg = load_config(
        overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")}
    )
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_audio_mcq_heatmap_uses_language_columns_and_percentages(mcqa_heatmap_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(mcqa_heatmap_server)
        page.wait_for_selector("#heatmap canvas")
        option = page.evaluate(
            """() => {
                 const chart = echarts.getInstanceByDom(document.getElementById('heatmap'));
                 const option = chart.getOption();
                 return {
                   axis: option.xAxis[0].data,
                   percent: option.visualMap[0].formatter(0.465),
                 };
               }"""
        )
        assert option == {"axis": ["de", "fr"], "percent": "47"}
        browser.close()


def test_matrix_blocks_are_visually_separated_and_headers_are_bold(live_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(live_server)
        page.wait_for_selector(".matrix-block")
        styles = page.evaluate(
            """() => {
                 const blocks = [...document.querySelectorAll('.matrix-block')];
                 const title = blocks[0].querySelector('.matrix-title');
                 return {
                   firstMargin: getComputedStyle(blocks[0]).marginTop,
                   secondMargin: getComputedStyle(blocks[1]).marginTop,
                   weight: getComputedStyle(title).fontWeight,
                 };
               }"""
        )
        assert styles["firstMargin"] == "0px"
        assert styles["secondMargin"] != "0px"
        assert int(styles["weight"]) >= 700
        browser.close()


@pytest.fixture
def trends_server(tmp_path):
    def asr(model, eval_model, wer, created):
        make_log(
            tmp_path,
            model=model,
            eval_model=eval_model,
            benchmark="librispeech-validation-clean",
            slice_id="asr-librispeech-validation-clean",
            task="asr",
            dataset_id=None,
            src=None,
            tgt=None,
            lang="en",
            metrics={"corpus_wer_raw": wer},
            campaign="speechval-trends",
            created=created,
        )

    asr("fondue-step-3000", "melt//fondue/checkpoint-3000", 0.20, "2026-11-10T10:00:00+00:00")
    asr("fondue-step-17000", "melt//fondue/checkpoint-17000", 0.15, "2026-11-10T11:00:00+00:00")
    asr("eurollm-speech-16n-step-0108000", "smurf//sweep/step=108000.ckpt", 0.18, "2026-11-10T12:00:00+00:00")
    asr("eurollm-speech-16n-step-0162000", "smurf//sweep/step=162000.ckpt", 0.14, "2026-11-10T13:00:00+00:00")
    for step, de, mt in [(102000, 30.0, 20.0), (162000, 32.0, 10.0), (270000, 31.0, 9.0)]:
        for lang, value in (("de", de), ("mt", mt)):
            make_log(
                tmp_path,
                model=f"eurollm-speech-16n-step-{step:06d}",
                eval_model="smurf//sweep/step.ckpt",
                benchmark="fleurs24-st-xen-dev",
                slice_id=f"st-fleurs-{lang}_en",
                task="st",
                dataset_id=f"fleurs-{lang}_en",
                src=lang,
                tgt="en",
                metrics={"corpus_bleu": value},
                campaign="speechval-trends",
                created=f"2026-11-11T{step // 60000:02d}:00:00+00:00",
            )
    cfg = dataclasses.replace(
        load_config(
            overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")}
        ),
        trend_metrics={},
    )
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_trends_page_renders_panels_and_one_line_per_lineage(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        assert page.locator(".nav a[aria-current='page']").inner_text() == "Trends"
        assert page.locator(".trends-controls #tab-languages").count() == 1
        assert page.locator("#lineage-menu").count() == 1
        assert page.locator(".trend-panel[data-family='LibriSpeech']").count() == 1
        assert page.locator(".trend-panel[data-family='FLEURS XtoEN']").count() == 1
        option = page.evaluate(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 return echarts.getInstanceByDom(el).getOption();
               }"""
        )
        assert sorted(series["name"] for series in option["series"]) == ["MELT/Fondue", "SMURF · 16n"]
        assert option["xAxis"][0]["type"] == "value"
        assert option["xAxis"][0]["name"] == "Progress (%)"
        browser.close()


def _trend_panel_option(page, family):
    return page.evaluate(
        """(family) => {
             const el = document.querySelector(`.trend-panel[data-family="${family}"] .trend-chart`);
             return echarts.getInstanceByDom(el).getOption();
           }""",
        family,
    )


def _open_lineages(page):
    if page.locator("#lineage-menu").get_attribute("open") is None:
        page.click("#lineage-menu summary")


def test_trends_hiding_a_lineage_removes_its_series(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        _open_lineages(page)
        page.uncheck("input.lineage-toggle[data-lineage='smurf/16n']")
        page.wait_for_function(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 return echarts.getInstanceByDom(el).getOption().series.length === 1;
               }"""
        )
        option = _trend_panel_option(page, "LibriSpeech")
        assert [series["name"] for series in option["series"]] == ["MELT/Fondue"]
        browser.close()


def test_trends_marks_best_and_worst_in_the_metric_direction(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        page.click("#x-step")
        page.wait_for_function(
            """() => document.querySelector('.trend-panel .trend-chart') &&
                     echarts.getInstanceByDom(document.querySelector('.trend-panel .trend-chart'))
                       .getOption().xAxis[0].name === 'Step'"""
        )
        option = _trend_panel_option(page, "LibriSpeech")
        series = next(item for item in option["series"] if item["name"] == "MELT/Fondue")
        coords = [item["coord"] for item in series["markPoint"]["data"]]
        assert [17000, 0.15] in coords
        assert [3000, 0.20] in coords
        browser.close()


def test_trends_progress_is_the_default_and_step_switches(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        option = _trend_panel_option(page, "LibriSpeech")
        assert option["xAxis"][0]["name"] == "Progress (%)"
        xs = [point[0] for series in option["series"] for point in series["data"]]
        assert xs and max(xs) <= 100
        page.click("#x-step")
        page.wait_for_function(
            """() => document.querySelector('.trend-panel .trend-chart') &&
                     echarts.getInstanceByDom(document.querySelector('.trend-panel .trend-chart'))
                       .getOption().xAxis[0].name === 'Step'"""
        )
        option = _trend_panel_option(page, "LibriSpeech")
        xs = [point[0] for series in option["series"] for point in series["data"]]
        assert max(xs) > 100
        browser.close()


def test_trends_leaderboard_rank_ignores_hidden_lineages(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        assert page.locator("#leaderboard tbody tr[data-step='162000']").count() == 1
        _open_lineages(page)
        page.uncheck("input.lineage-toggle[data-lineage='smurf/16n']")
        page.wait_for_function(
            "() => document.querySelector('#leaderboard tbody tr').dataset.step === '17000'"
        )
        browser.close()


def test_trends_leaderboard_starts_at_top_five_and_expands(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        assert page.locator("#leaderboard tbody tr").count() == 5
        page.click("#leaderboard-toggle")
        assert page.locator("#leaderboard tbody tr").count() > 5
        assert "Show top 5" in page.inner_text("#leaderboard-toggle")
        browser.close()


def test_trends_pinning_a_checkpoint_draws_it_on_every_panel(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        page.click("#x-step")
        page.click("#leaderboard tbody tr[data-step='162000']")
        option = _trend_panel_option(page, "LibriSpeech")
        marks = [
            mark["xAxis"]
            for series in option["series"]
            for mark in (series.get("markLine") or {}).get("data", [])
        ]
        assert 162000 in marks
        browser.close()


def test_trends_languages_tab_lists_weakness_and_onset(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        page.click("#tab-languages")
        page.wait_for_selector("#language-list .lang-row")
        rows = page.locator("#language-list .lang-row")
        assert rows.first.get_attribute("data-lang") == "mt"
        onset = page.inner_text("#language-list .lang-row[data-lang='mt'] .lang-onset")
        assert "162000" in onset
        option = page.evaluate(
            """() => {
                 const el = document.getElementById('language-chart');
                 return echarts.getInstanceByDom(el).getOption();
               }"""
        )
        assert sorted(series["name"] for series in option["series"]) == ["de", "mt"]
        browser.close()


@pytest.fixture
def trends_gap_server(tmp_path):
    def log(model, benchmark, slice_id, task, metrics, created, lang=None, src=None, tgt=None):
        make_log(
            tmp_path,
            model=model,
            eval_model="melt//fondue/checkpoint",
            benchmark=benchmark,
            slice_id=slice_id,
            task=task,
            dataset_id=None if task == "asr" else f"fleurs-{src}_en",
            src=src,
            tgt=tgt,
            lang=lang,
            metrics=metrics,
            campaign="speechval-gap",
            created=created,
        )

    for step, wer, created in [
        (3000, 0.20, "2026-11-10T10:00:00+00:00"),
        (17000, 0.15, "2026-11-10T11:00:00+00:00"),
        (51000, 0.10, "2026-11-10T12:00:00+00:00"),
    ]:
        log(f"fondue-step-{step}", "librispeech-validation-clean",
            "asr-librispeech-validation-clean", "asr",
            {"corpus_wer_raw": wer}, created, lang="en")
    for step, bleu, created in [
        (3000, 20.0, "2026-11-10T13:00:00+00:00"),
        (51000, 30.0, "2026-11-10T14:00:00+00:00"),
    ]:
        log(f"fondue-step-{step}", "fleurs24-st-xen-dev",
            "st-fleurs-de_en", "st", {"corpus_bleu": bleu}, created, src="de", tgt="en")
    cfg = dataclasses.replace(
        load_config(
            overrides={"roots": [str(tmp_path)], "include": ["*"], "cache": str(tmp_path / "w.sqlite")}
        ),
        trend_metrics={},
    )
    store = Store(cfg.cache)
    store.sync([row for path in iter_log_paths(cfg) if (row := scan_run(path))])
    server = make_server("127.0.0.1", 0, create_app(cfg, store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_trends_does_not_connect_across_a_missing_checkpoint(trends_gap_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_gap_server}/trends")
        page.wait_for_selector(".trend-panel")
        page.click("#x-step")
        page.wait_for_function(
            """() => document.querySelector('.trend-panel .trend-chart') &&
                     echarts.getInstanceByDom(document.querySelector('.trend-panel .trend-chart'))
                       .getOption().xAxis[0].name === 'Step'"""
        )
        option = _trend_panel_option(page, "FLEURS XtoEN")
        data = option["series"][0]["data"]
        assert [17000, None] in data
        browser.close()


def test_trends_lineage_colour_is_stable_across_panels(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        librispeech = _trend_panel_option(page, "LibriSpeech")
        fleurs = _trend_panel_option(page, "FLEURS XtoEN")
        first = next(series for series in librispeech["series"] if series["name"] == "SMURF · 16n")
        second = next(series for series in fleurs["series"] if series["name"] == "SMURF · 16n")
        assert first["itemStyle"]["color"] == second["itemStyle"]["color"]
        browser.close()


def test_trends_panel_legend_hides_a_lineage(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        page.evaluate(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 const chart = echarts.getInstanceByDom(el);
                 chart.dispatchAction({type: 'legendToggleSelect', name: 'SMURF · 16n'});
               }"""
        )
        page.wait_for_function(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 return echarts.getInstanceByDom(el).getOption().series.length === 1;
               }"""
        )
        browser.close()


def test_trends_language_tab_drops_a_hidden_lineage(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector("#leaderboard tbody tr")
        page.click("#tab-languages")
        page.wait_for_selector("#language-list .lang-row")
        _open_lineages(page)
        page.uncheck("input.lineage-toggle[data-lineage='smurf/16n']")
        page.wait_for_function(
            "() => document.querySelectorAll('#lang-lineage option').length === 0"
        )
        assert "No visible lineage" in page.inner_text("#language-list")
        browser.close()


def test_trends_slider_zoom_is_shared(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        page.evaluate(
            """() => {
                 const charts = [...document.querySelectorAll('.trend-chart')]
                   .map((el) => echarts.getInstanceByDom(el));
                 charts[0].dispatchAction({type: 'dataZoom', dataZoomIndex: 2, start: 20, end: 80});
               }"""
        )
        page.wait_for_function(
            """() => {
                 const charts = [...document.querySelectorAll('.trend-chart')]
                   .map((el) => echarts.getInstanceByDom(el));
                 const zoom = charts[1].getOption().dataZoom[0];
                 return Math.round(zoom.start) === 20 && Math.round(zoom.end) === 80;
               }"""
        )
        browser.close()


def test_trends_panels_are_grouped_under_task_headings(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        headings = [text.lower() for text in page.locator(".trend-task-heading").all_inner_texts()]
        assert "speech recognition" in headings
        assert "speech translation" in headings
        browser.close()


def test_trends_tooltip_reports_coverage(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        text = page.evaluate(
            """() => {
                 const el = document.querySelector('.trend-panel[data-family="LibriSpeech"] .trend-chart');
                 const chart = echarts.getInstanceByDom(el);
                 chart.dispatchAction({type: 'showTip', seriesIndex: 0, dataIndex: 0});
                 return chart.getDom().innerText;
               }"""
        )
        assert "1/1" in text
        browser.close()


def test_trends_controls_have_padding_and_the_lineage_menu_is_not_clipped(trends_server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1200})
        page.goto(f"{trends_server}/trends")
        page.wait_for_selector(".trend-panel")
        padding = page.evaluate(
            "getComputedStyle(document.querySelector('.trends-controls')).paddingTop"
        )
        assert padding != "0px"
        page.click("#lineage-summary")
        assert page.locator("#lineage-menu").get_attribute("open") is not None
        assert page.locator("#lineage-toggles").is_visible()
        hit = page.evaluate(
            """() => {
                 const el = document.querySelector('input.lineage-toggle');
                 const rect = el.getBoundingClientRect();
                 const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
                 return !!(hit && (hit === el || (hit.closest('label') && hit.closest('label').contains(el))));
               }"""
        )
        assert hit
        browser.close()
