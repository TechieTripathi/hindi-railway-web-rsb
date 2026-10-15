import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ui


class ArticleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        registry = {}
        for name, pipeline in ui.PIPELINES.items():
            cfg = SimpleNamespace(
                DAYS_BACK=pipeline["cfg"].DAYS_BACK,
                OUTPUT_DIR=self.temp.name,
                CRAWL_OUTPUT=os.path.join(self.temp.name, name + "_crawl.json"),
                AGENCY_OUTPUT=os.path.join(self.temp.name, name + "_agency.json"),
                SCORED_OUTPUT=os.path.join(self.temp.name, name + "_scored.json"),
                STATUS_FILE=os.path.join(self.temp.name, name + "_status.json"),
            )
            registry[name] = dict(pipeline, cfg=cfg)
        self.pipelines = patch.object(ui, "PIPELINES", registry)
        self.pipelines.start()
        self.addCleanup(self.pipelines.stop)
        current = patch.object(ui, "CURRENT", None)
        current.start()
        self.addCleanup(current.stop)
        # Run history drives the panel (last settings, failed runs), so it must come
        # from the temp dir, never from the real output/logs.
        self.log_dir = os.path.join(self.temp.name, "logs")
        os.makedirs(self.log_dir)
        for target, value in (("LOG_DIR", self.log_dir), ("JOBS", {}), ("JOB_ORDER", [])):
            patcher = patch.object(ui, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = ui.app.test_client()
        self.arts = [
            {"title": "New railway station opened in Delhi", "link": "https://example.com/a",
             "source": "Publisher A", "date": "2026-10-04", "body": "Delhi station details " * 30},
            {"title": "Freight loading reaches annual record", "link": "https://example.com/b",
             "source": "Publisher B", "date": "2026-10-03", "body": "Freight details " * 30},
            {"title": "Special festival train service announced", "link": "https://example.com/c",
             "source": "Publisher C", "date": "2026-10-02", "body": "Train service details " * 30},
        ]
        for name in ("web", "rsb"):
            self.write_crawl(name, self.arts)

    def write_crawl(self, name, arts):
        Path(ui.PIPELINES[name]["cfg"].CRAWL_OUTPUT).write_text(
            json.dumps(arts), encoding="utf-8")

    def select(self, name, action, indices=()):
        return self.client.post("/api/selection/" + name, json={
            "action": action, "keys": [ui._article_key(self.arts[i]) for i in indices]})

    def test_stable_keys_and_fallback(self):
        self.assertEqual(ui._article_key(self.arts[0]),
                         ui._article_key(dict(self.arts[0], title="Changed", body="Changed")))
        without_link = dict(self.arts[0], link="")
        self.assertEqual(ui._article_key(without_link),
                         ui._article_key(dict(without_link, body="Changed")))
        self.assertNotEqual(ui._article_key(without_link),
                            ui._article_key(dict(without_link, date="2026-10-05")))

    def test_default_selection_persistence_and_original_untouched(self):
        for name in ("web", "rsb"):
            with self.subTest(pipeline=name):
                path = Path(ui.PIPELINES[name]["cfg"].CRAWL_OUTPUT)
                original = path.read_bytes()
                self.assertEqual(self.client.get("/api/selection/" + name).json["selected"], 3)
                self.assertFalse(Path(ui._selection_path(name)).exists())
                self.assertEqual(self.select(name, "exclude", [0, 2]).json["selected"], 1)
                self.assertEqual(ui._selected_articles(name), [self.arts[1]])
                self.assertEqual(self.client.get("/api/selection/" + name).json["excluded"], 2)
                self.assertEqual(self.select(name, "include", [2]).json["selected"], 2)
                for action in ("select_all", "clear_excluded"):
                    self.select(name, "exclude", [0])
                    self.assertEqual(self.select(name, action).json["selected"], 3)
                self.assertEqual(path.read_bytes(), original)

    def test_pipeline_isolation_and_stale_exclusion_pruning(self):
        self.select("web", "exclude", [0, 2])
        self.assertEqual(len(ui._selected_articles("rsb")), 3)
        self.write_crawl("web", self.arts[1:])
        summary = self.client.get("/api/selection/web").json
        self.assertEqual(summary["excluded_keys"], [ui._article_key(self.arts[2])])
        self.write_crawl("web", self.arts)
        self.assertEqual(len(ui._selected_articles("web")), 2)

    def test_refresh_during_crawl_preserves_saved_exclusions(self):
        self.select("web", "exclude", [0])
        sidecar = Path(ui._selection_path("web"))
        original = sidecar.read_bytes()
        with patch.object(ui, "CURRENT", SimpleNamespace(pipeline="web", step="crawl")):
            self.write_crawl("web", [])
            self.client.get("/api/selection/web")
            self.assertEqual(sidecar.read_bytes(), original)
            self.write_crawl("web", self.arts)
            self.assertEqual(len(ui._selected_articles("web")), 2)
            ui._excluded_keys("web", self.arts[1:], after_crawl=True)
        self.assertEqual(json.loads(sidecar.read_text())["excluded_keys"], [])

    def test_estimates_and_generation_inputs_in_every_mode(self):
        self.select("web", "exclude", [0])
        cfg = ui.PIPELINES["web"]["cfg"]
        Path(cfg.AGENCY_OUTPUT).write_text(json.dumps([
            dict(self.arts[1], ai_news_story="A completed news report with real content.")]),
            encoding="utf-8")
        estimate = ui._run_estimate("web")
        self.assertEqual(estimate["total"], 2)
        for mode in ui.clustering.MODES:
            with self.subTest(mode=mode):
                groups = ui.clustering.build_clusters(self.arts[1:], mode=mode)
                self.assertEqual(estimate["counts"][mode], len(groups))
                self.assertEqual(estimate["calls"][mode], sum(
                    ui._resume_key(c["title"]) != ui._resume_key(self.arts[1]["title"])
                    for c in groups))
                job = SimpleNamespace(pipeline="web", log=Mock())
                path = ui._cluster_input(job, cfg, mode)
                generated = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertNotEqual(path, cfg.CRAWL_OUTPUT)
                self.assertEqual(len(generated), len(groups))
                self.assertNotIn(self.arts[0]["title"], [a["title"] for a in generated])
                self.assertNotIn(self.arts[0]["body"], " ".join(a["body"] for a in generated))
                if mode == "off":
                    self.assertEqual(generated, self.arts[1:])

    def test_empty_selection_blocks_ai_before_key_or_engine(self):
        self.select("web", "exclude", [0, 1, 2])
        with patch.object(ui.engine, "run_generate") as generate, \
                patch.object(ui.config_env, "apply_to_engine") as key:
            self.assertEqual(self.client.post("/run/web/generate", json={}).status_code, 400)
            with self.assertRaisesRegex(ValueError, "No articles selected"):
                ui._step_generate(SimpleNamespace(pipeline="web", log=Mock()), {}, "web")
            generate.assert_not_called()
            key.assert_not_called()

    def test_generation_passes_selected_file_to_engine(self):
        self.select("web", "exclude", [0, 2])
        job = SimpleNamespace(pipeline="web", log=Mock())
        for mode in ui.clustering.MODES:
            with self.subTest(mode=mode), \
                    patch.object(ui.config_env, "apply_to_engine", return_value=True), \
                    patch.object(ui.engine, "STATUS_FILE", "unused"), \
                    patch.object(ui.engine, "run_generate", return_value=[]) as generate:
                ui._step_generate(job, {"cluster_mode": mode}, "web")
                source = generate.call_args.args[0]
                data = json.loads(Path(source).read_text(encoding="utf-8"))
                self.assertEqual([a["title"] for a in data], [self.arts[1]["title"]])

    def test_views_filters_and_preview_original_indices(self):
        for name in ("web", "rsb"):
            self.select(name, "exclude", [0])
            selected = self.client.get("/data/" + name + "?stage=crawl&selection=selected")
            self.assertEqual(selected.status_code, 200)
            html = selected.get_data(as_text=True)
            self.assertIn("2 selected for AI", html)
            self.assertNotIn(self.arts[0]["title"], html)
            excluded = self.client.get("/data/" + name + "?stage=crawl&selection=excluded")
            self.assertIn(self.arts[0]["title"], excluded.get_data(as_text=True))
            preview = self.client.get("/clusters/" + name + "?mode=off").get_data(as_text=True)
            self.assertIn("/verify/" + name + "/1?stage=crawl", preview)
            self.assertNotIn("/verify/" + name + "/0?stage=crawl", preview)
            self.assertEqual(self.client.get("/panel/" + name).status_code, 200)

    def test_invalid_requests_and_busy_run(self):
        self.assertEqual(self.client.get("/api/selection/missing").status_code, 404)
        for payload in ([1], {}, {"action": []}, {"action": "exclude", "keys": "bad"},
                        {"action": "exclude", "keys": ["stale"]}):
            self.assertEqual(self.client.post("/api/selection/web", json=payload).status_code, 400)
        with patch.object(ui, "CURRENT", SimpleNamespace(pipeline="web", step="crawl")):
            self.assertEqual(self.select("web", "exclude", [0]).status_code, 409)

    def test_new_fetch_clears_only_its_pipeline_and_preserves_backups(self):
        for name in ("web", "rsb"):
            for result in (self.arts[1:], []):
                with self.subTest(pipeline=name, fetched=len(result)):
                    cfg = ui.PIPELINES[name]["cfg"]
                    other = ui.PIPELINES["rsb" if name == "web" else "web"]["cfg"]
                    previous = [dict(self.arts[0], ai_news_story="A completed news report with real content.")]
                    for path in (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT,
                                 other.AGENCY_OUTPUT, other.SCORED_OUTPUT):
                        Path(path).write_text(json.dumps(previous), encoding="utf-8")
                    original = Path(cfg.AGENCY_OUTPUT).read_bytes()
                    module = ui.config_web if name == "web" else ui.config_rsb
                    pipeline = ui.web_pipeline if name == "web" else ui.rsb_pipeline
                    method = "run_hindi_crawl" if name == "web" else "run_rsb_crawl"
                    step = ui._step_web_crawl if name == "web" else ui._step_rsb_crawl
                    backups = os.path.join(self.temp.name, "backups")

                    def fetch(**kwargs):
                        self.assertFalse(Path(cfg.AGENCY_OUTPUT).exists())
                        self.assertFalse(Path(cfg.SCORED_OUTPUT).exists())
                        self.write_crawl(name, result)
                        return result

                    with patch.object(module, "CRAWL_OUTPUT", cfg.CRAWL_OUTPUT), \
                            patch.object(ui, "BACKUP_DIR", backups), \
                            patch.object(pipeline, method, side_effect=fetch):
                        step(SimpleNamespace(pipeline=name, log=Mock()), {})
                    self.assertEqual(Path(other.AGENCY_OUTPUT).read_bytes(), original)
                    self.assertEqual(Path(other.SCORED_OUTPUT).read_bytes(), original)
                    for path in (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT):
                        saved = list(Path(backups).glob(Path(path).stem + ".*.json"))
                        self.assertEqual(len(saved), 1)
                        self.assertEqual(saved[0].read_bytes(), original)
                    state = ui._state(name)
                    self.assertEqual(state["steps"][1]["view"], 0)
                    self.assertEqual(state["steps"][2]["view"], 0)
                    self.assertFalse(state["steps"][2]["enabled"])
                    estimate = state["estimate"]
                    self.assertEqual(estimate["done"], 0)
                    self.assertEqual(estimate["calls"], estimate["counts"])

    def test_failed_fetch_still_clears_previous_results(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        for path in (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT):
            Path(path).write_text(json.dumps(self.arts), encoding="utf-8")
        with patch.object(ui.config_web, "CRAWL_OUTPUT", cfg.CRAWL_OUTPUT), \
                patch.object(ui, "BACKUP_DIR", os.path.join(self.temp.name, "backups")), \
                patch.object(ui.web_pipeline, "run_hindi_crawl", side_effect=RuntimeError("fetch failed")):
            with self.assertRaisesRegex(RuntimeError, "fetch failed"):
                ui._step_web_crawl(SimpleNamespace(pipeline="web", log=Mock()), {})
        self.assertFalse(Path(cfg.AGENCY_OUTPUT).exists())
        self.assertFalse(Path(cfg.SCORED_OUTPUT).exists())

    def test_panel_and_result_links_count_only_selected_reports(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        generated = [dict(a, ai_news_story="A completed news report with real content.")
                     for a in self.arts]
        generated += [dict(generated[0], title="Old story %d" % i) for i in range(45)]
        for path in (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT):
            Path(path).write_text(json.dumps(generated), encoding="utf-8")
        self.select("web", "exclude", [0, 2])
        estimate = ui._run_estimate("web")
        for mode in ui.clustering.MODES:
            self.assertEqual(estimate["skipped"][mode], 1)
            self.assertEqual(estimate["checked"][mode], 1)
        self.assertEqual(estimate["done"], 1)
        html = self.client.get("/panel/web").get_data(as_text=True)
        self.assertIn('id="step-review"', html)
        self.assertIn("<strong>1 story</strong>", html)       # Review lists only this selection
        self.assertNotIn("Old story", html)
        self.assertNotIn("48 stories", html)
        self.assertNotIn("48 already written", html)
        for stage in ("agency", "scored"):
            html = self.client.get("/data/web?stage=%s&selection=selected&mode=story" % stage).get_data(as_text=True)
            self.assertIn(self.arts[1]["title"], html)
            self.assertNotIn(self.arts[0]["title"], html)
            self.assertNotIn("Old story", html)

    def test_startup_clears_stale_generation_and_its_newer_scores(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        for path in (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT):
            Path(path).write_text(json.dumps(self.arts), encoding="utf-8")
        os.utime(cfg.AGENCY_OUTPUT, (1, 1))
        os.utime(cfg.CRAWL_OUTPUT, (2, 2))
        os.utime(cfg.SCORED_OUTPUT, (3, 3))
        with patch.object(ui, "BACKUP_DIR", os.path.join(self.temp.name, "backups")), \
                patch("builtins.print"):
            ui._clear_stale_outputs("web")
        self.assertFalse(Path(cfg.AGENCY_OUTPUT).exists())
        self.assertFalse(Path(cfg.SCORED_OUTPUT).exists())
        self.assertEqual(len(list(Path(self.temp.name, "backups").glob("*.json"))), 2)
        self.assertEqual(ui._run_estimate("web")["done"], 0)

    def test_startup_preserves_results_generated_after_fetch(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        Path(cfg.AGENCY_OUTPUT).write_text(json.dumps(self.arts), encoding="utf-8")
        os.utime(cfg.CRAWL_OUTPUT, (1, 1))
        os.utime(cfg.AGENCY_OUTPUT, (2, 2))
        ui._clear_stale_outputs("web")
        self.assertTrue(Path(cfg.AGENCY_OUTPUT).exists())

    def test_fetched_page_offers_discard_and_restore(self):
        page = lambda q="": self.client.get("/data/web?stage=crawl" + q).get_data(as_text=True)
        html = page()
        self.assertIn("3 selected for AI · 0 discarded", html)
        self.assertEqual(html.count('data-action="exclude"'), 3)   # one Discard per row
        self.assertIn("restore-all", html)
        self.assertIn("hidden>Restore all (0)", html)               # nothing to restore yet

        self.select("web", "exclude", [0])
        html = page()
        self.assertIn("2 selected for AI · 1 discarded", html)
        self.assertIn("Discarded — won’t be written", html)
        self.assertEqual(html.count('data-action="include"'), 1)
        self.assertNotIn("hidden>Restore all", html)
        discarded = page("&selection=excluded")
        self.assertIn(self.arts[0]["title"], discarded)
        self.assertNotIn(self.arts[1]["title"], discarded)

        panel = self.client.get("/panel/web").get_data(as_text=True)
        self.assertIn("2 selected · 1 discarded", panel)           # step 2, open
        self.assertEqual(panel.count('data-action="include"'), 1)
        self.assertIn("Continue with <span class=\"sel-continue-n\">2</span>", panel)

    def test_discarding_everything_explains_how_to_continue(self):
        self.select("web", "exclude", [0, 1, 2])
        panel = self.client.get("/panel/web").get_data(as_text=True)
        self.assertIn("You discarded every article", panel)
        self.assertIn("Every article is discarded — restore at least one", panel)   # step 3 locked
        with self.assertRaisesRegex(ValueError, "Restore at least one discarded article"):
            ui._step_generate(SimpleNamespace(pipeline="web", log=Mock()), {}, "web")

    def test_extraction_fact_matches_what_each_pipeline_records(self):
        generic = [dict(a, extractor="generic") for a in self.arts]
        self.write_crawl("web", generic)
        self.write_crawl("rsb", [dict(a, body_hindi_ratio=0.97, is_hindi_body=True)
                                 for a in self.arts])
        web = self.client.get("/data/web?stage=crawl").get_data(as_text=True)
        rsb = self.client.get("/data/rsb?stage=crawl").get_data(as_text=True)
        self.assertIn("selector has drifted", web)
        self.assertNotIn("Devanagari</span>", web)
        self.assertIn("97% Devanagari", rsb)
        if "search" in ui.PIPELINES:
            self.write_crawl("search", generic)
            search = self.client.get("/data/search?stage=crawl").get_data(as_text=True)
            self.assertNotIn("0% Devanagari", search)              # the old bug
            self.assertIn("normal for open-web results", search)
            self.assertNotIn("selector has drifted", search)


    # ------------------------------------------------------ guided panel

    def _story(self, art):
        return dict(art, ai_news_story="A completed news report with real content in it.")

    def _write_job(self):
        return SimpleNamespace(pipeline="web", log=Mock(), progress={})

    def test_write_step_checks_quality_automatically(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        def generate(src, dst):
            Path(dst).write_text(json.dumps([self._story(self.arts[0])]), encoding="utf-8")
            return []
        job = self._write_job()
        with patch.object(ui.config_env, "apply_to_engine", return_value=True), \
                patch.object(ui.engine, "run_generate", side_effect=generate), \
                patch.object(ui.score_report, "rescore_file",
                             return_value=([], cfg.SCORED_OUTPUT)) as rescore:
            summary = ui.STEPS[("web", "generate")](job, {})
        rescore.assert_called_once_with(cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT)
        self.assertEqual(job.progress["stage"], "check")
        self.assertIn("quality:", summary)

    def test_quality_check_skipped_when_nothing_was_written(self):
        with patch.object(ui.config_env, "apply_to_engine", return_value=True), \
                patch.object(ui.engine, "run_generate", side_effect=RuntimeError("API down")), \
                patch.object(ui.score_report, "rescore_file") as rescore:
            with self.assertRaisesRegex(RuntimeError, "API down"):
                ui.STEPS[("web", "generate")](self._write_job(), {})
        rescore.assert_not_called()

    def test_partial_failure_still_checks_the_paid_stories(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        def generate(src, dst):
            Path(dst).write_text(json.dumps([self._story(self.arts[0])]), encoding="utf-8")
            raise RuntimeError("stopped half way")
        with patch.object(ui.config_env, "apply_to_engine", return_value=True), \
                patch.object(ui.engine, "run_generate", side_effect=generate), \
                patch.object(ui.score_report, "rescore_file",
                             return_value=([], cfg.SCORED_OUTPUT)) as rescore:
            with self.assertRaisesRegex(RuntimeError, "half way"):
                ui.STEPS[("web", "generate")](self._write_job(), {})
        rescore.assert_called_once()

    def test_failed_quality_check_does_not_fail_the_paid_write(self):
        def generate(src, dst):
            Path(dst).write_text(json.dumps([self._story(self.arts[0])]), encoding="utf-8")
            return []
        with patch.object(ui.config_env, "apply_to_engine", return_value=True), \
                patch.object(ui.engine, "run_generate", side_effect=generate), \
                patch.object(ui.score_report, "rescore_file", side_effect=ValueError("bad file")):
            summary = ui.STEPS[("web", "generate")](self._write_job(), {})
        self.assertIn("quality check failed", summary)

    def test_write_and_check_run_as_one_job(self):
        import threading
        cfg = ui.PIPELINES["web"]["cfg"]
        def generate(src, dst):
            Path(dst).write_text(json.dumps([self._story(self.arts[0])]), encoding="utf-8")
            return []
        with patch.object(ui.config_env, "apply_to_engine", return_value=True), \
                patch.object(ui.engine, "run_generate", side_effect=generate), \
                patch.object(ui.score_report, "rescore_file",
                             return_value=([], cfg.SCORED_OUTPUT)):
            job = ui._run_async("web", "generate", {})
            for t in threading.enumerate():
                if t.name == job.id:
                    t.join(timeout=10)
        self.assertEqual(job.state, "ok", job.error)
        self.assertTrue(job.log_name.endswith("-web-generate.log"))
        self.assertTrue(any("Checking quality" in line for line in job.lines))
        self.assertEqual(job.progress["done"], 0)       # the check line is not counted as progress
        self.assertTrue(os.path.exists(os.path.join(self.log_dir, job.log_name)))

    def test_technical_details_hidden_by_default(self):
        import re
        html = self.client.get("/panel/web").get_data(as_text=True)
        self.assertIn("/static/theme-init.js", html)                 # applies it before paint
        self.assertIn("/static/ui.css", html)
        css = self.client.get("/static/ui.css").get_data(as_text=True)
        self.assertIn(":root:not([data-advanced]) [data-adv] { display:none !important; }", css)
        init = self.client.get("/static/theme-init.js").get_data(as_text=True)
        self.assertIn("localStorage.getItem('advanced')", init)
        self.assertRegex(html, r'<a class="nav-item" href="/logs"[^>]*data-adv>')
        self.assertRegex(html, r'<a class="nav-item" href="/data/web"[^>]*data-adv>')
        self.assertIn('Technical details: <span class="adv-state">off</span>', html)
        self.assertIsNone(re.search(r'<a class="nav-item" href="/"[^>]*aria-current', html))

    def test_a_technical_page_still_shows_its_own_nav_item(self):
        html = self.client.get("/logs").get_data(as_text=True)
        self.assertRegex(html, r'<a class="nav-item" href="/logs" title="Run history" aria-current="page">')

    # ------------------------------------------------------ app shell (look)

    def test_static_assets_are_served(self):
        for path in ("/static/ui.css", "/static/ui.js", "/static/theme-init.js",
                     "/static/fonts/inter-latin-400.woff2",
                     "/static/fonts/noto-sans-devanagari-400.woff2"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            r.close()

    def test_sidebar_lists_every_source_and_marks_the_open_one(self):
        import re
        html = self.client.get("/panel/search").get_data(as_text=True)
        for name in ui.PIPELINES:
            self.assertIn('href="/panel/%s"' % name, html)
        self.assertRegex(html, r'href="/panel/search" title="[^"]+"\s+aria-current="page"')
        self.assertIsNone(re.search(r'href="/panel/web" title="[^"]+"\s+aria-current', html))
        self.assertIn("Keyword search", html)                       # the short nav name
        home = self.client.get("/").get_data(as_text=True)
        self.assertRegex(home, r'<a class="nav-item" href="/" title="Home" aria-current="page">')

    def test_status_pill_names_the_running_step(self):
        job = SimpleNamespace(pipeline="web", step="generate", id="web-generate-001",
                              log_name="x.log")
        with patch.object(ui, "CURRENT", job):
            html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Writing stories · Hindi news publishers", html)
        self.assertIn('class="nav-dot"', html)

    def test_icon_macro_renders_inline_svg(self):
        from flask import render_template_string
        with ui.app.test_request_context():
            out = render_template_string(
                "{% from '_icons.html' import icon %}{{ icon('search', 18) }}{{ icon('nope') }}")
        self.assertEqual(out.count("<svg"), 2)
        self.assertIn('width="18"', out)

    def test_run_history_is_grouped_by_day(self):
        import datetime
        for stamp, step in (("20260901-101010", "crawl"), ("20260902-090000", "generate")):
            name = "%s-web-%s.log" % (stamp, step)
            Path(self.log_dir, name).write_text("ok\n", encoding="utf-8")
            Path(self.log_dir, name[:-4] + ".json").write_text(json.dumps(
                {"pipeline": "web", "step": step, "state": "ok", "summary": "fine"}), encoding="utf-8")
        html = self.client.get("/logs").get_data(as_text=True)
        for day in (datetime.date(2026, 9, 1), datetime.date(2026, 9, 2)):
            self.assertIn("%s, %d %s" % (day.strftime("%A"), day.day, day.strftime("%B %Y")), html)
        self.assertIn("Write stories", html)
        self.assertIn('<span class="time">10:10</span>', html)

    def test_paid_and_destructive_steps_use_the_dialog(self):
        html = self.client.get("/panel/web?open=write").get_data(as_text=True)
        self.assertIn("ui.confirm(", html)
        self.assertNotIn("!confirm(", html)

    def test_panel_opens_the_step_that_matters(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        focus = lambda **kw: ui._flow("web", **kw)["focus"]
        self.assertEqual(focus(), "choose")                       # articles found, nothing written
        os.remove(cfg.CRAWL_OUTPUT)
        flow = ui._flow("web")
        self.assertEqual(flow["focus"], "find")
        self.assertEqual(flow["steps"]["write"]["lock"], "Find and choose articles first.")
        self.write_crawl("web", self.arts)
        self.select("web", "exclude", [0, 1, 2])
        flow = ui._flow("web")
        self.assertEqual(flow["focus"], "choose")
        self.assertIn("restore at least one", flow["steps"]["write"]["lock"])
        self.select("web", "clear_excluded")
        Path(cfg.AGENCY_OUTPUT).write_text(
            json.dumps([self._story(a) for a in self.arts]), encoding="utf-8")
        flow = ui._flow("web", mode="off")
        self.assertEqual(flow["focus"], "review")
        self.assertEqual(flow["counts"]["unchecked"], 3)         # written, not yet checked
        with patch.object(ui, "CURRENT", SimpleNamespace(pipeline="web", step="generate", id="x")):
            flow = ui._flow("web", open_step="find")
            self.assertEqual(flow["focus"], "write")              # a running job wins
            self.assertFalse(flow["can_run"])

    def test_open_parameter_reopens_finished_steps_only(self):
        self.assertEqual(ui._flow("web", open_step="find")["focus"], "find")
        self.assertEqual(ui._flow("web", open_step="review")["focus"], "choose")   # locked: ignored
        self.assertEqual(ui._flow("web", open_step="nonsense")["focus"], "choose")
        html = self.client.get("/panel/web?open=find").get_data(as_text=True)
        import re
        self.assertRegex(html, r'class="fstep is-open[^"]*"\s+id="step-find"')
        self.assertNotRegex(html, r'class="fstep is-open[^"]*"\s+id="step-choose"')

    def test_write_step_states_the_cost_and_needs_a_key(self):
        html = self.client.get("/panel/web?open=write").get_data(as_text=True)
        self.assertIn("Paid step", html)
        self.assertIn('data-calls=\'{"off": 3, "story": 3, "topic": 3}\'', html)
        with patch.object(ui.config_env, "describe", return_value={"set": False, "length": 0,
                                                                   "source": "nowhere", "name": "x"}):
            r = self.client.post("/run/web/generate", json={})
            self.assertEqual(r.status_code, 400)
            self.assertIn("No Anthropic API key", r.json["error"])
            self.assertIn("No Anthropic API key", ui._flow("web")["steps"]["write"]["lock"])

    def test_review_lists_quality_numbers_in_plain_words(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        scored = [dict(self._story(a), coverage_score=72, plagiarism_score=41, fact_score=90,
                       gates_passed=True, gate_failures=[]) for a in self.arts]
        for path in (cfg.AGENCY_OUTPUT, cfg.SCORED_OUTPUT):
            Path(path).write_text(json.dumps(scored), encoding="utf-8")
        html = self.client.get("/panel/web").get_data(as_text=True)
        self.assertIn('id="step-review"', html)
        self.assertIn("Facts kept <b>72%</b>", html)
        self.assertIn("Copied text <b>41%</b>", html)                # over 35%: needs a look
        self.assertIn("3 need a closer look", html)
        self.assertIn("/verify/web/0?stage=scored", html)

    def test_old_routes_still_work(self):
        for url in ("/", "/data/web", "/data/web/0", "/logs", "/clusters/web", "/scores",
                    "/verify/web/0", "/panel/rsb", "/panel/search"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_run_history_uses_current_step_names(self):
        name = "20260901-101010-web-crawl.log"
        Path(self.log_dir, name).write_text("x\n", encoding="utf-8")
        Path(self.log_dir, name[:-4] + ".json").write_text(json.dumps(
            {"pipeline": "web", "step": "crawl", "step_label": "Fetch articles",
             "state": "ok", "params": {"days": "3"}}), encoding="utf-8")
        self.assertEqual(ui._run_brief_from_disk(name)["step_label"], "Find articles")
        self.assertIn("last 3 days", ui._flow("web")["steps"]["find"]["summary"])

    def test_plain_error_keeps_only_the_sentence(self):
        tb = 'Traceback (most recent call last):\n  File "x", line 1\nValueError: No articles selected for AI.'
        self.assertEqual(ui._plain_error(tb), "No articles selected for AI.")
        self.assertEqual(ui._plain_error(""), "")


    def test_choose_list_is_built_for_long_lists(self):
        self.select("web", "exclude", [2])
        html = self.client.get("/panel/web").get_data(as_text=True)
        self.assertIn('class="text-filter"', html)
        self.assertIn('<option value="Publisher A">Publisher A (1)</option>', html)  # source filter
        self.assertIn('data-source="Publisher B"', html)
        self.assertIn('data-text="freight loading reaches annual record publisher b"', html)
        self.assertIn("table-scroll", html)                    # list scrolls in its own box
        # Only the exception is badged: one visible "Discarded", kept rows' badges hidden.
        self.assertEqual(html.count("Discarded — won’t be written</span>"), 1)
        self.assertEqual(html.count('badge-success" hidden'), 2)
        self.assertIn('Selected <span class="count">2</span>', html)


    def test_story_tabs_follow_the_text_not_the_field(self):
        english_source = {"source_lang": "en",
                          "ai_headline": "Railways adds sleeper coaches",
                          "ai_news_story": "Indian Railways will add sleeper coaches next year.",
                          "ai_headline_translated": "रेलवे स्लीपर कोच जोड़ेगा",
                          "ai_news_story_translated": "भारतीय रेलवे अगले साल स्लीपर कोच जोड़ेगा।"}
        tabs = ui._ai_story_tabs(english_source)
        self.assertEqual([(t["label"], t["key"]) for t in tabs],
                         [("Hindi", "translated"), ("English", "story")])
        self.assertEqual(tabs[0]["note"], "translation of the English story")
        # A "translation" in the same language as the story is flagged, not hidden.
        untranslated = dict(english_source, ai_news_story_translated="Still English text here.",
                            ai_headline_translated="Still English")
        labels = [t["label"] for t in ui._ai_story_tabs(untranslated)]
        self.assertEqual(labels, ["English", "English (translation)"])
        # Review shows the Hindi headline even when the source was English.
        cfg = ui.PIPELINES["web"]["cfg"]
        Path(cfg.AGENCY_OUTPUT).write_text(json.dumps([dict(self.arts[0], **english_source)]),
                                           encoding="utf-8")
        self.assertEqual(ui._rows("web", "agency")[0]["ai_headline"], "रेलवे स्लीपर कोच जोड़ेगा")

    def test_social_posts_carry_both_languages_for_the_toggle(self):
        english_source = {"source_lang": "en",
                          "social_twitter": "Railways adds sleeper coaches #Rail",
                          "social_twitter_translated": "रेलवे स्लीपर कोच जोड़ेगा #Rail",
                          "social_general": "Sleeper coaches arrive next year.",
                          "social_general_translated": "Still English, not translated."}
        posts = {p["label"]: p for p in ui._ai_social(english_source)}
        # Labelled by the text, not the field: the Hindi post came from *_translated.
        tw = posts["Twitter / X post"]["panels"]
        self.assertEqual([(p["lang"], p["shown"]) for p in tw], [("hi", True), ("en", False)])
        self.assertEqual(tw[0]["text"], "रेलवे स्लीपर कोच जोड़ेगा #Rail")
        # A "translation" in the same language is dropped; the one post stays shown.
        gen = posts["General post"]["panels"]
        self.assertEqual([(p["lang"], p["shown"]) for p in gen], [("en", True)])
        self.assertNotIn("Facebook post", posts)
        # The page renders a panel per language, hidden ones marked hidden.
        cfg = ui.PIPELINES["web"]["cfg"]
        story = {"ai_headline": "Railways adds sleeper coaches",
                 "ai_news_story": "Indian Railways will add sleeper coaches next year.",
                 "ai_headline_translated": "रेलवे स्लीपर कोच जोड़ेगा",
                 "ai_news_story_translated": "भारतीय रेलवे अगले साल स्लीपर कोच जोड़ेगा।",
                 "fact_details": "ok", "topics": ["Operations"]}
        Path(cfg.AGENCY_OUTPUT).write_text(
            json.dumps([dict(self.arts[0], **english_source, **story)]), encoding="utf-8")
        html = self.client.get("/verify/web/0?stage=agency").get_data(as_text=True)
        self.assertIn('data-social-lang="hi"\n             >', html)
        self.assertIn('data-social-lang="en"\n             hidden>', html)
        self.assertIn('data-lang="hi"', html)                  # story tab carries its language
        self.assertNotIn("answer was cut off", html)

    def test_cut_off_answer_is_flagged_on_review(self):
        cfg = ui.PIPELINES["web"]["cfg"]
        art = dict(self.arts[0], ai_headline="रेलवे", truncated=True,
                   ai_news_story="भारतीय रेलवे अगले साल स्लीपर कोच जोड़ेगा। " * 3)
        Path(cfg.AGENCY_OUTPUT).write_text(json.dumps([art]), encoding="utf-8")
        html = self.client.get("/verify/web/0?stage=agency").get_data(as_text=True)
        self.assertIn("answer was cut off", html)


if __name__ == "__main__":
    unittest.main()
