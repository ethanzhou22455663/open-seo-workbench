"""REST API and built SPA host. Frontend development remains a separate project."""

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .analysis import FACETS, PRESETS, analyse, explain_priority
from .imports import import_file, preview_file
from .services import (add_metric_definition, add_task, import_runs, list_pages, list_sources,
                       list_tasks, metric_definitions, metric_series, page_detail,
                       set_source_preference, site_settings, update_settings)
from .storage import create_site, list_sites, site_db
from .adapters import adapter_names

app = FastAPI(title="Open SEO Workbench", version=__version__, description="Provider-neutral, multi-site SEO data API")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])


class SiteCreate(BaseModel):
    name: str
    domain: str
    timezone: str = "UTC"


class SettingsUpdate(BaseModel):
    values: dict


class PreferenceUpdate(BaseModel):
    metric_key: str
    geo: str
    sources: list[str]


class MetricCreate(BaseModel):
    metric_key: str
    label: str
    entity_scope: str
    unit: str
    aggregation: str = "sum"
    description: str = ""


class TaskCreate(BaseModel):
    keyword_id: int | None = None
    page_id: int | None = None
    proposed_page_type: str | None = None
    note: str = ""


class PageUpdate(BaseModel):
    page_type: str | None = None
    language: str | None = None
    business_priority: int | None = Field(default=None, ge=1, le=5)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error_handler(_request: Request, exc: sqlite3.IntegrityError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": str(exc)}, status_code=409)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/sites")
def sites():
    return list_sites()


@app.get("/api/adapters")
def adapters():
    return adapter_names()


@app.post("/api/sites", status_code=201)
def new_site(payload: SiteCreate):
    return create_site(payload.name, payload.domain, payload.timezone)


@app.get("/api/sites/{site_id}/settings")
def settings(site_id: str):
    return site_settings(site_id)


@app.put("/api/sites/{site_id}/settings")
def save_settings(site_id: str, payload: SettingsUpdate):
    return update_settings(site_id, payload.values)


@app.get("/api/sites/{site_id}/sources")
def sources(site_id: str):
    return list_sources(site_id)


@app.put("/api/sites/{site_id}/source-preferences")
def source_preference(site_id: str, payload: PreferenceUpdate):
    return set_source_preference(site_id, payload.metric_key, payload.geo, payload.sources)


@app.get("/api/sites/{site_id}/metrics")
def metrics(site_id: str):
    return metric_definitions(site_id)


@app.post("/api/sites/{site_id}/metrics", status_code=201)
def new_metric(site_id: str, payload: MetricCreate):
    return add_metric_definition(site_id, **payload.model_dump())


@app.get("/api/sites/{site_id}/pages")
def pages(site_id: str, month: str | None = None, geo: str | None = None, search: str = "",
          page_type: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
          search_source: str | None = None, inquiry_source: str | None = None):
    return list_pages(site_id, month, geo, search, page_type, limit, offset, search_source, inquiry_source)


@app.get("/api/sites/{site_id}/pages/{page_id}")
def page(site_id: str, page_id: int, month: str | None = None, geo: str | None = None,
         search_source: str | None = None, query_source: str | None = None,
         demand_source: str | None = None, demand_geo: str | None = None,
         inquiry_source: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    return page_detail(site_id, page_id, month, geo, search_source, query_source,
                       demand_source, demand_geo, inquiry_source, limit, offset)


@app.patch("/api/sites/{site_id}/pages/{page_id}")
def save_page(site_id: str, page_id: int, payload: PageUpdate):
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return {"ok": True}
    with site_db(site_id) as db:
        if not db.execute("SELECT 1 FROM pages WHERE page_id=?", (page_id,)).fetchone():
            raise ValueError("Unknown page_id")
        with db:
            for key, value in updates.items():
                if key == "page_type" and (not value or len(value) > 40):
                    raise ValueError("Invalid page_type")
                db.execute(f"UPDATE pages SET {key}=? WHERE page_id=?", (value, page_id))
            if "page_type" in updates:
                db.execute("UPDATE pages SET page_type_origin='manual' WHERE page_id=?", (page_id,))
            from .storage import audit
            audit(db, "user", "update_page", {"page_id": page_id, **updates})
    return {"ok": True}


@app.get("/api/sites/{site_id}/presets")
def presets(site_id: str, month: str | None = None, geo: str | None = None, demand_geo: str | None = None,
            search_source: str | None = None, query_source: str | None = None,
            demand_source: str | None = None, inquiry_source: str | None = None):
    return [{"id": key, "name": name, "level": level,
             "count": analyse(site_id, month=month, geo=geo, demand_geo=demand_geo,
                              search_source=search_source, query_source=query_source,
                              demand_source=demand_source, inquiry_source=inquiry_source,
                              preset=key, limit=1)["total"]}
            for key, name, level in PRESETS]


@app.get("/api/facets")
def facets():
    return {key: sorted(values) for key, values in FACETS.items()}


@app.get("/api/sites/{site_id}/opportunities")
def opportunities(site_id: str, month: str | None = None, geo: str | None = None,
                  demand_geo: str | None = None, search_source: str | None = None,
                  query_source: str | None = None, demand_source: str | None = None,
                  inquiry_source: str | None = None, preset: str | None = None,
                  page_type: str | None = None, search: str = "", facets: str = "{}",
                  limit: int = Query(100, ge=1, le=500),
                  offset: int = Query(0, ge=0)):
    return analyse(site_id, month, geo, demand_geo, search_source, query_source,
                   demand_source, inquiry_source, preset, page_type, search, json.loads(facets), limit, offset)


@app.get("/api/sites/{site_id}/pages/{page_id}/priority")
def priority(site_id: str, page_id: int, month: str | None = None, geo: str | None = None,
             demand_geo: str | None = None):
    return explain_priority(site_id, page_id, month, geo, demand_geo)


@app.get("/api/sites/{site_id}/metric-series")
def series(site_id: str, metric_key: str, entity_id: int, geo: str | None = None,
           source_name: str | None = None, keyword_id: int | None = None,
           limit: int = Query(36, ge=1, le=120)):
    return metric_series(site_id, metric_key, entity_id, geo, source_name, limit, keyword_id)


@app.get("/api/sites/{site_id}/imports")
def runs(site_id: str):
    return import_runs(site_id)


@app.post("/api/import-preview")
async def preview(request: Request, filename: str):
    blob = await request.body()
    if len(blob) > 20_000_000:
        raise HTTPException(413, "File is too large")
    return preview_file(blob, filename)


@app.post("/api/sites/{site_id}/imports/{dataset}")
async def upload(site_id: str, dataset: str, request: Request, filename: str, source_name: str,
                 provider: str = "file", geo: str = "GLOBAL", grain: str = "month",
                 default_period: str | None = None, coverage: str = "partial",
                 x_column_mapping: str = Header("{}")):
    blob = await request.body()
    if len(blob) > 20_000_000:
        raise HTTPException(413, "File is too large")
    return import_file(site_id, blob, filename, dataset=dataset, source_name=source_name,
                       provider=provider, geo=geo, grain=grain, default_period=default_period,
                       coverage=coverage, mapping=json.loads(x_column_mapping))


@app.get("/api/sites/{site_id}/tasks")
def tasks(site_id: str):
    return list_tasks(site_id)


@app.post("/api/sites/{site_id}/tasks", status_code=201)
def new_task(site_id: str, payload: TaskCreate):
    return add_task(site_id, **payload.model_dump())


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    target = (FRONTEND_DIST / path).resolve()
    if FRONTEND_DIST.exists() and target.is_relative_to(FRONTEND_DIST.resolve()) and target.is_file():
        return FileResponse(target)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<h1>Open SEO Workbench</h1><p>Build the frontend with <code>npm run build</code> in frontend/.</p>")
