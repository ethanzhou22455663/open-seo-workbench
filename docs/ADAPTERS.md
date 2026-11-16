# Provider adapter contract

The core never imports an Ahrefs, GSC or CRM client. Optional provider packages register a Python entry point in the group `open_seo_workbench.adapters`. The entry point resolves to a class with `name`, `provider` and `fetch(...)`.

```python
from seo_platform.adapters import AdapterBatch

class ExampleSearchAdapter:
    name = "Example Search API"
    provider = "example_search"

    def fetch(self, *, site, settings, start, end, geo):
        # Read credentials from the environment or OS credential store.
        # Respect provider quotas and record only requested periods.
        rows = [{"page_url": "https://example.com/product/a/", "period": "2026-10",
                 "clicks": 7, "impressions": 120, "position": 9.1}]
        yield AdapterBatch("search_page", rows, geo=geo, coverage="complete")
```

Register in the provider package's `pyproject.toml`:

```toml
[project.entry-points."open_seo_workbench.adapters"]
example_search = "my_provider:ExampleSearchAdapter"
```

Run `seo-workbench adapters` to list installed providers and `seo-workbench sync-adapter SITE_ID example_search 2026-10 2026-10 --geo US` to import. The MCP tools `list_provider_adapters` and `sync_provider` expose the same operation; synchronization is a dry run by default. API calls that may incur charges belong inside the provider package, which can implement its own cost guard and retries. Return one `AdapterBatch` per dataset, geography and period scope. A batch marked `complete` replaces that scope after validation; use `partial` for incremental data.
