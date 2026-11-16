# Open SEO Workbench

A local-first, multi-site SEO data workbench. Search Console, Ahrefs, analytics platforms, rank trackers and CRM exports are **sources**, not the data model. The web UI, REST API and MCP server all call the same Python services.

## Start

Requires Python 3.11+, Node.js and npm. On Windows, run `./start.ps1`; on macOS/Linux, run `./start.sh`. The launcher creates the Python environment on first use, builds the TypeScript frontend and opens `http://127.0.0.1:8766/`. Development mode: run `seo-workbench serve --no-browser` and `npm run dev` inside `frontend/` separately. REST documentation is at `/docs`.

Data defaults to the user's application data directory (`%LOCALAPPDATA%\open-seo-workbench` on Windows, `$XDG_DATA_HOME/open-seo-workbench` on Linux). Set `SEO_PLATFORM_DATA_DIR` to choose another location. The launcher automatically uses local `data/` only if it already contains a registry database. `data/`, databases, exports, credentials and logs are Git ignored.

## Sites and data model

One registry lists sites. **Each site has its own SQLite database** with page and keyword IDs, URL aliases, data sources, import runs, page/search-query observations, keyword demand, inquiries, custom metric definitions, tasks and rule versions. Every API and MCP operation receives an explicit `site_id`.

An observation has a source, geography, period and grain. Search clicks and impressions belong to page observations; page × keyword average position belongs to query observations. Keyword volume is a supplier estimate with a snapshot month. `GLOBAL` means all countries *within that source*. The UI labels search geography and demand geography independently. Source preferences are configured per metric and geography. Different sources are never silently added together.

Missing is `null`, distinct from an observed zero. With no inquiry source, a page's inquiry count is unknown and lead-dependent opportunities do not fire. With no demand source, ranking and CTR opportunities can still fire, while volume-gated product opportunities cannot. The opportunity response includes `missing_inputs`, source names, rule version and evidence.

## Import files without an API key

The UI accepts CSV, TSV, XLSX and JSON. The same normalized importer is available through REST, MCP and CLI. Choose a dataset, source name, geography and month; map source column names to the canonical fields when needed. The default `partial` coverage upserts rows; `complete` coverage replaces the same source, geography, grain and periods after parsing succeeds. A repeated identical import is idempotent.

| Dataset | Required fields | Optional fields |
| --- | --- | --- |
| `search_page` | `page_url`, `period`, one metric | `clicks`, `impressions`, `position`, `page_type`, `language` |
| `search_page_keyword` | `page_url`, `keyword`, `period`, one metric | `clicks`, `impressions`, `position` |
| `keyword_demand` | `keyword`, `period` | `volume`, `difficulty`, `cpc` |
| `inquiry` | an inquiry row | `external_id`, `submitted_on`, `landing_url`, `submit_url`, `quality`, `attribution` |
| `custom_metric` | registered `metric_key`, entity URL or keyword, `period` | `value`, `value_text`, `entity_scope` |

`period` may be omitted when supplying a default month/date during import. `page` and `query` columns are recognized as aliases for `page_url` and `keyword`. A GSC JSON response with `page` or `query,page` dimensions is accepted. Split multi-month GSC responses into separate monthly exports; assigning the aggregate to one month would misstate trends. The current calendar month shows observations but suppresses month-over-month trend and decline rules. `NORMAL` inquiries are page-linked records; they are not automatically attributed to organic search.

Example CLI import:

```text
seo-workbench create-site "Example Store" example.com
seo-workbench sites
seo-workbench import SITE_ID search_page pages.csv --source "Search export" --geo US --period 2026-10
seo-workbench import SITE_ID keyword_demand demand.csv --source "Volume export" --geo US --period 2026-11
```

New metrics use `POST /api/sites/{site_id}/metrics`, then the `custom_metric` importer. Installed API connectors use the entry-point contract in [docs/ADAPTERS.md](docs/ADAPTERS.md); they feed the same importer. No provider credentials are stored in this repository.

## Opportunities and filters

Eight evidence-gated combinations cover inquiry-page recovery, product ranking potential, purchasing-content decay, low CTR, second-page keywords, traffic without recorded inquiries, one query on multiple pages and zero-click product terms. The page table has three attention colors. Multi-tag filters use OR within a group and AND across groups. Page type patterns, purchase-intent terms, product-page informational-query exclusions, geography and numeric thresholds are site settings; rule changes create a version. Tags use only available data and display `unknown` where necessary. The opportunity table's inquiry count uses the configured lookback window; the summary card and page inquiry history use calendar months.

## MCP

Local AI clients can start `seo-workbench mcp` over stdio. The server exposes site listing, page and query detail, opportunities, priority evidence, metric series, import status and optional provider adapters. Write tools support `dry_run` by default and record their actions. Every site-scoped tool requires `site_id`.

Example client configuration after installation:

```json
{"mcpServers":{"seo-workbench":{"command":"seo-workbench","args":["mcp"],"env":{"SEO_PLATFORM_DATA_DIR":"/path/to/private/data"}}}}
```

For local HTTP clients, run `seo-workbench mcp --transport streamable-http --port 8767` and connect to `http://127.0.0.1:8767/mcp`. The current launcher binds REST and MCP HTTP to loopback only; remote deployment needs authentication and is intentionally a separate configuration.

## Migrate the previous single-site database

`seo-workbench migrate-legacy /path/to/old.sqlite3 --name "My Site" --domain example.com --timezone UTC` reads the old database in read-only mode and creates a new site database. It preserves page IDs, keyword IDs, monthly/daily search data, demand snapshots, inquiries, aliases and tasks, then checks row counts. The old database remains in place. Keep the migration target data directory outside a public Git repository or in ignored `data/`.

## Development and publication

`python -m unittest discover -s tests -v` runs data, API and MCP tests. `cd frontend && npm ci && npm run build` type-checks and builds the UI. Run `python scripts/check_public_repo.py` after `git add` and before pushing. The repository contains synthetic test domains only; real site databases, uploads, exports and credentials must stay outside Git.

This is a local single-user application. SQLite WAL supports concurrent readers and a writer on one host; do not put its live database on a network share. Multi-user remote hosting would need an authenticated service and a deployment-specific database plan.
