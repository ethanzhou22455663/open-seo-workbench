import asyncio
import json
import os
import tempfile
import unittest

from mcp import Client
from seo_platform.mcp_server import mcp
from seo_platform.storage import create_site


class McpTests(unittest.TestCase):
    def test_tools_and_explicit_site_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("SEO_PLATFORM_DATA_DIR")
            os.environ["SEO_PLATFORM_DATA_DIR"] = directory
            try:
                site = create_site("Example", "example.org")
                async def check():
                    async with Client(mcp) as client:
                        tools = await client.list_tools()
                        names = {tool.name for tool in tools.tools}
                        self.assertIn("get_page_keywords", names)
                        self.assertIn("get_opportunities", names)
                        result = await client.call_tool("get_site_overview", {"site_id":site["site_id"]})
                        self.assertFalse(result.is_error)
                        payload = result.structured_content or json.loads(result.content[0].text)
                        self.assertEqual(payload["total"], 0)
                asyncio.run(check())
            finally:
                if previous is None: os.environ.pop("SEO_PLATFORM_DATA_DIR", None)
                else: os.environ["SEO_PLATFORM_DATA_DIR"] = previous


if __name__ == "__main__": unittest.main()
