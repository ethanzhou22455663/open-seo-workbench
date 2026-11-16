"""MCP tools backed by the exact same services as the web API."""

from pathlib import Path

from mcp.server import MCPServer

from .analysis import analyse, explain_priority
from .imports import import_file, preview_file
from .services import (add_task, import_runs, list_pages as query_pages, metric_series,
                       page_detail, site_settings, update_settings)
from .storage import create_site as register_site, list_sites as all_sites
from .adapters import adapter_names, sync_adapter

mcp = MCPServer("open-seo-workbench")


@mcp.tool(description="List available site IDs and domains. Pass a site_id explicitly to every site tool.")
def list_sites() -> list[dict]:
    return all_sites()


@mcp.tool(description="Read a site's configuration, including its default geography and rule thresholds.")
def get_site_settings(site_id: str) -> dict:
    return site_settings(site_id)


@mcp.tool(description="Read page totals and the selected source for one site, month and geography. Missing values are null.")
def get_site_overview(site_id: str, month: str | None = None, geo: str | None = None) -> dict:
    return query_pages(site_id, month=month, geo=geo, limit=1)


@mcp.tool(description="List pages for a site. Results are paginated and include source-specific search metrics.")
def list_pages(site_id: str, month: str | None = None, geo: str | None = None,
               search: str = "", page_type: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    return query_pages(site_id, month=month, geo=geo, search=search,
                       page_type=page_type, limit=limit, offset=offset)


@mcp.tool(description="Read one page and its visible query rankings, clicks, impressions, demand and inquiry history.")
def get_page_keywords(site_id: str, page_id: int, month: str | None = None,
                      geo: str | None = None, demand_geo: str | None = None,
                      limit: int = 50, offset: int = 0) -> dict:
    return page_detail(site_id, page_id, month=month, geo=geo,
                       demand_geo=demand_geo, limit=limit, offset=offset)


@mcp.tool(description="List evidence-gated SEO opportunities; source and missing-input evidence accompanies each page.")
def get_opportunities(site_id: str, month: str | None = None, geo: str | None = None,
                      demand_geo: str | None = None, preset: str | None = None,
                      facets: dict[str, list[str]] | None = None,
                      limit: int = 50, offset: int = 0) -> dict:
    return analyse(site_id, month=month, geo=geo, demand_geo=demand_geo,
                   preset=preset, facets=facets, limit=limit, offset=offset)


@mcp.tool(description="Explain why a specific page is high, medium or normal priority, including missing inputs.")
def explain_page_priority(site_id: str, page_id: int, month: str | None = None,
                          geo: str | None = None, demand_geo: str | None = None) -> dict:
    return explain_priority(site_id, page_id, month, geo, demand_geo)


@mcp.tool(description="Read a time series for one registered metric and entity; returns provenance and geography.")
def get_metric_series(site_id: str, metric_key: str, entity_id: int,
                      geo: str | None = None, source_name: str | None = None,
                      limit: int = 36, keyword_id: int | None = None) -> dict:
    return metric_series(site_id, metric_key, entity_id, geo, source_name, limit, keyword_id)


@mcp.tool(description="List recent data imports with source, range, coverage and status.")
def get_import_status(site_id: str, limit: int = 50) -> list[dict]:
    return import_runs(site_id, limit)


@mcp.tool(description="List optional installed API provider adapters.")
def list_provider_adapters() -> list[str]:
    return adapter_names()


@mcp.tool(description="Synchronize an installed provider adapter for one site and date range. Dry run by default; an adapter may incur provider charges.")
def sync_provider(site_id: str, adapter_name: str, start: str, end: str,
                  geo: str = "GLOBAL", dry_run: bool = True) -> dict:
    if adapter_name not in adapter_names():
        raise ValueError("Unknown provider adapter")
    if dry_run:
        return {"dry_run": True, "site_id": site_id, "adapter": adapter_name,
                "start": start, "end": end, "geo": geo}
    return sync_adapter(site_id, adapter_name, start, end, geo)


@mcp.tool(description="Create a site database. Use dry_run=true to validate without writing.")
def create_site(name: str, domain: str, timezone: str = "UTC", dry_run: bool = True) -> dict:
    if dry_run:
        from zoneinfo import ZoneInfo
        from .storage import normalize_domain
        ZoneInfo(timezone)
        return {"dry_run": True, "name": name, "domain": normalize_domain(domain), "timezone": timezone}
    return register_site(name, domain, timezone)


@mcp.tool(description="Preview or import a local CSV, TSV, XLSX or JSON file into an explicit site and dataset.")
def import_local_file(site_id: str, path: str, dataset: str, source_name: str,
                      geo: str = "GLOBAL", grain: str = "month", default_period: str | None = None,
                      mapping: dict[str, str] | None = None, coverage: str = "partial",
                      dry_run: bool = True) -> dict:
    file = Path(path).expanduser().resolve()
    if not file.is_file() or file.stat().st_size > 20_000_000:
        raise ValueError("File is missing or larger than 20 MB")
    blob = file.read_bytes()
    if dry_run:
        return {"dry_run": True, **preview_file(blob, file.name)}
    return import_file(site_id, blob, file.name, dataset=dataset, source_name=source_name,
                       geo=geo, grain=grain, default_period=default_period,
                       mapping=mapping, coverage=coverage, actor="mcp")


@mcp.tool(description="Update site-specific URL patterns, intent terms, geographies or rule thresholds.")
def set_site_settings(site_id: str, values: dict, dry_run: bool = True) -> dict:
    if dry_run:
        return {"dry_run": True, "current": site_settings(site_id), "proposed": values}
    return update_settings(site_id, values, actor="mcp")


@mcp.tool(description="Create a keyword/page optimization task after reviewing its target. Dry run by default.")
def create_keyword_task(site_id: str, keyword_id: int | None = None, page_id: int | None = None,
                        proposed_page_type: str | None = None, note: str = "",
                        dry_run: bool = True) -> dict:
    if dry_run:
        return {"dry_run": True, "site_id": site_id, "keyword_id": keyword_id,
                "page_id": page_id, "proposed_page_type": proposed_page_type, "note": note}
    return add_task(site_id, keyword_id, page_id, proposed_page_type, note, actor="mcp")
