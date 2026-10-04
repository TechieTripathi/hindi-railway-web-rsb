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
        self.assertIn("1 of 1 selected reports have a story", html)
        self.assertNotIn("48 already written", html)
        self.assertNotIn("48 of 48", html)
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


if __name__ == "__main__":
    unittest.main()
