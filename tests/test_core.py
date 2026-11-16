import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from seo_platform.analysis import analyse
from seo_platform.imports import import_file, import_records
from seo_platform.services import (add_metric_definition, list_pages, metric_series,
                                   page_detail, set_source_preference)
from seo_platform.storage import create_site, list_sites, site_db


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("SEO_PLATFORM_DATA_DIR")
        os.environ["SEO_PLATFORM_DATA_DIR"] = self.tmp.name
        self.site = create_site("Shop A", "example.com")
        self.other = create_site("Shop B", "second.example")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("SEO_PLATFORM_DATA_DIR", None)
        else:
            os.environ["SEO_PLATFORM_DATA_DIR"] = self.previous
        self.tmp.cleanup()

    def test_sites_are_isolated_and_missing_is_not_zero(self):
        sid = self.site["site_id"]
        rows = [{"page_url": "https://example.com/product/shower/", "period": "2026-10", "clicks": 2,
                 "impressions": 1000, "position": 11}]
        result = import_records(sid, rows, dataset="search_page", source_name="GSC export", coverage="complete")
        self.assertEqual(result["row_count"], 1)
        self.assertTrue(import_records(sid, rows, dataset="search_page", source_name="GSC export", coverage="complete")["duplicate"])
        self.assertEqual(list_pages(sid)["total"], 1)
        self.assertEqual(list_pages(self.other["site_id"])["total"], 0)
        page = list_pages(sid)["pages"][0]
        self.assertEqual(page["clicks"], 2)
        self.assertIsNone(page["normal_inquiries"])
        self.assertIsNone(page_detail(sid, page["page_id"])["hidden_query_clicks"])
        opportunity = analyse(sid)["rows"][0]
        self.assertIn("inquiry", opportunity["missing_inputs"])
        self.assertNotIn("traffic_no_leads", opportunity["matched_presets"])

    def test_geographies_and_source_preferences_are_explicit(self):
        sid = self.site["site_id"]
        page = "https://example.com/product/shower/"
        import_records(sid, [{"page_url":page,"period":"2026-10","clicks":2,"impressions":1000}],
                       dataset="search_page", source_name="GSC")
        import_records(sid, [{"page_url":page,"period":"2026-10","clicks":9,"impressions":1000}],
                       dataset="search_page", source_name="Other provider")
        import_records(sid, [{"page_url":page,"period":"2026-10","clicks":6,"impressions":500}],
                       dataset="search_page", source_name="GSC", geo="US")
        import_records(sid, [{"page_url":page,"keyword":"shower manufacturer","period":"2026-10",
                              "clicks":0,"impressions":100,"position":9}],
                       dataset="search_page_keyword", source_name="GSC")
        import_records(sid, [{"keyword":"shower manufacturer","period":"2026-11","volume":350}],
                       dataset="keyword_demand", source_name="Volume provider", geo="US")
        self.assertEqual(list_pages(sid)["summary"]["search_clicks"], 2)
        self.assertEqual(list_pages(sid, geo="US")["summary"]["search_clicks"], 6)
        set_source_preference(sid, "search_clicks", "GLOBAL", ["Other provider", "GSC"])
        self.assertEqual(list_pages(sid)["summary"]["search_clicks"], 9)
        pid = list_pages(sid)["pages"][0]["page_id"]
        detail = page_detail(sid, pid, demand_geo="US")
        self.assertEqual(detail["keywords"][0]["demand"]["volume"], 350)
        self.assertIsNone(detail["hidden_query_clicks"])

    def test_duplicate_file_fingerprint_and_custom_metric(self):
        sid = self.site["site_id"]
        row = [{"page_url":"https://example.com/", "period":"2026-10", "clicks":0}]
        first = import_records(sid, row, dataset="search_page", source_name="GSC")
        second = import_records(sid, row, dataset="search_page", source_name="GSC")
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["run_id"], second["run_id"])
        pid = list_pages(sid)["pages"][0]["page_id"]
        add_metric_definition(sid, "organic_sessions", "Organic sessions", "page", "count")
        import_records(sid, [{"page_url":"https://example.com/","period":"2026-10",
                              "metric_key":"organic_sessions","value":15}],
                       dataset="custom_metric", source_name="Analytics")
        self.assertEqual(metric_series(sid, "organic_sessions", pid)["values"][0]["value"], 15)
        with site_db(sid) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM search_page_metrics").fetchone()[0], 1)

    def test_partial_month_and_import_provenance(self):
        sid = self.site["site_id"]
        month = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m")
        previous = "2026-01"
        page = "https://example.com/product/shower/"
        import_records(sid, [{"page_url": page, "period": previous, "clicks": 100},
                             {"page_url": page, "period": month, "clicks": 1}],
                       dataset="search_page", source_name="GSC")
        result = analyse(sid, month=month)
        self.assertTrue(result["partial_month"])
        self.assertEqual(result["rows"][0]["click_trend"], "unknown")
        import_records(sid, [{"external_id": "lead-1", "submitted_on": "2026-10-16",
                             "landing_url": page, "quality": "NORMAL"}],
                       dataset="inquiry", source_name="CRM")
        with site_db(sid) as db:
            row = db.execute("SELECT period_start,period_end FROM import_runs WHERE dataset='inquiry'").fetchone()
            self.assertEqual(tuple(row), ("2026-10", "2026-10"))

    def test_gsc_multimonth_is_rejected_even_with_default_period(self):
        payload = b'{"request":{"startDate":"2026-09-01","endDate":"2026-10-31","dimensions":["page"]},"rows":[{"keys":["https://example.com/"],"clicks":3}]}'
        with self.assertRaisesRegex(ValueError, "multiple periods"):
            import_file(self.site["site_id"], payload, "gsc.json", dataset="search_page",
                        source_name="GSC", default_period="2026-10")

    def test_daily_only_data_rolls_up_without_inventing_monthly_rows(self):
        sid = self.site["site_id"]
        page = "https://example.com/product/shower/"
        import_records(sid, [
            {"page_url":page,"period":"2026-10-01","clicks":2,"impressions":10,"position":4},
            {"page_url":page,"period":"2026-10-02","clicks":3,"impressions":30,"position":8}],
            dataset="search_page", source_name="Daily search", grain="day")
        import_records(sid, [
            {"page_url":page,"keyword":"shower supplier","period":"2026-10-01","clicks":1,"impressions":10,"position":4},
            {"page_url":page,"keyword":"shower supplier","period":"2026-10-02","clicks":2,"impressions":30,"position":8}],
            dataset="search_page_keyword", source_name="Daily search", grain="day")
        overview = list_pages(sid)
        self.assertEqual(overview["month"], "2026-10")
        self.assertEqual(overview["summary"]["search_clicks"], 5)
        pid = overview["pages"][0]["page_id"]
        detail = page_detail(sid, pid)
        self.assertEqual(detail["keywords"][0]["clicks"], 3)
        self.assertEqual(detail["keywords"][0]["position"], 7)
        self.assertEqual(metric_series(sid, "search_clicks", pid)["values"][0]["value"], 5)
        self.assertEqual(metric_series(sid, "search_position", pid,
                                       keyword_id=detail["keywords"][0]["keyword_id"])["values"][0]["value"], 7)
        with site_db(sid) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM search_page_metrics WHERE grain='month'").fetchone()[0], 0)

    def test_inquiry_corrections_do_not_double_count(self):
        sid = self.site["site_id"]
        row = {"external_id":"lead-7","submitted_on":"2026-10-05",
               "landing_url":"https://example.com/product/shower/","quality":"SPAM"}
        import_records(sid, [row], dataset="inquiry", source_name="CRM")
        import_records(sid, [{**row, "quality":"NORMAL"}], dataset="inquiry", source_name="CRM")
        with site_db(sid) as db:
            self.assertEqual(db.execute("SELECT COUNT(*),quality FROM inquiries").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT quality FROM inquiries").fetchone()[0], "NORMAL")


if __name__ == "__main__":
    unittest.main()
