import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from seo_platform.api import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("SEO_PLATFORM_DATA_DIR")
        os.environ["SEO_PLATFORM_DATA_DIR"] = self.tmp.name
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        if self.previous is None:
            os.environ.pop("SEO_PLATFORM_DATA_DIR", None)
        else:
            os.environ["SEO_PLATFORM_DATA_DIR"] = self.previous
        self.tmp.cleanup()

    def test_site_import_page_drilldown_and_facet_filter(self):
        response = self.client.post("/api/sites", json={"name":"Example","domain":"example.com","timezone":"UTC"})
        self.assertEqual(response.status_code, 201, response.text)
        sid = response.json()["site_id"]
        settings = self.client.get(f"/api/sites/{sid}/settings").json()
        self.assertEqual(settings["rules_version"], 1)
        updated = self.client.put(f"/api/sites/{sid}/settings", json={"values":{
            "page_type_patterns":[{"pattern":"^/product/","type":"product"}]}})
        self.assertEqual(updated.status_code, 200, updated.text)
        body = b"page_url,keyword,period,clicks,impressions,position\nhttps://example.com/product/shower-supplier/,shower supplier,2026-10,0,120,9\n"
        upload = self.client.post(f"/api/sites/{sid}/imports/search_page_keyword",
            params={"filename":"queries.csv","source_name":"CSV source","geo":"US"},
            content=body, headers={"X-Column-Mapping":"{}"})
        self.assertEqual(upload.status_code, 200, upload.text)
        page_body = b"page_url,period,clicks,impressions\nhttps://example.com/product/shower-supplier/,2026-10,0,300\n"
        response = self.client.post(f"/api/sites/{sid}/imports/search_page",
            params={"filename":"pages.csv","source_name":"CSV source","geo":"US"}, content=page_body)
        self.assertEqual(response.status_code, 200, response.text)
        pages = self.client.get(f"/api/sites/{sid}/pages", params={"geo":"US","month":"2026-10"}).json()
        self.assertEqual(pages["total"], 1)
        self.assertIsNone(pages["pages"][0]["normal_inquiries"])
        page_id = pages["pages"][0]["page_id"]
        detail = self.client.get(f"/api/sites/{sid}/pages/{page_id}", params={"geo":"US","month":"2026-10"}).json()
        self.assertEqual(detail["keywords"][0]["position"], 9)
        self.assertEqual(detail["hidden_query_clicks"], 0)
        filtered = self.client.get(f"/api/sites/{sid}/opportunities",
            params={"geo":"US","month":"2026-10","facets":'{"traffic_tier":["none","low"],"rank_band":["first_page"]}'}).json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["rows"][0]["page_type"], "product")


if __name__ == "__main__": unittest.main()
