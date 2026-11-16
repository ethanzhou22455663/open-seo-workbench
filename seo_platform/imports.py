"""Provider-neutral file ingestion. API adapters can call import_records directly."""

import csv
import hashlib
import io
import json
from datetime import date, datetime

from .storage import audit, ensure_keyword, ensure_page, ensure_source, site_db, site_info, utc_now

DATASETS = {"search_page", "search_page_keyword", "keyword_demand", "inquiry", "custom_metric"}
GEO_GLOBAL = "GLOBAL"


def normalize_geo(value):
    geo = str(value or GEO_GLOBAL).strip().upper()
    if geo in {"ALL", "WORLD", "WW"}:
        geo = GEO_GLOBAL
    if geo != GEO_GLOBAL and (len(geo) != 2 or not geo.isalpha()):
        raise ValueError("geo must be GLOBAL or a two-letter country code")
    return geo


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _number(value, integer=False):
    raw = _text(value).replace(",", "")
    if raw in {"", "—", "-", "N/A", "n/a"}:
        return None
    result = float(raw.rstrip("%"))
    if integer:
        if result != int(result) or result < 0:
            raise ValueError(f"Expected non-negative integer, got {raw}")
        return int(result)
    return result


def read_file(blob, filename):
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix in {"csv", "tsv"}:
        text = blob.decode("utf-8-sig")
        dialect = csv.excel_tab if suffix == "tsv" else csv.excel
        return list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if suffix == "xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = [_text(value) for value in next(rows)]
        records = [dict(zip(headers, values)) for values in rows]
        workbook.close()
        return records
    if suffix == "json":
        data = json.loads(blob)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            dimensions = data.get("request", {}).get("dimensions", [])
            if dimensions in (["page"], ["query", "page"], ["page", "query"]):
                return [{**{dimension: row["keys"][index] for index, dimension in enumerate(dimensions)},
                         "clicks": row.get("clicks"), "impressions": row.get("impressions"),
                         "position": row.get("position"),
                         "period": data.get("request", {}).get("startDate")}
                        for row in data["rows"]]
            return data["rows"]
    raise ValueError("Upload CSV, TSV, XLSX, or a JSON array/GSC response")


def preview_file(blob, filename):
    rows = read_file(blob, filename)
    return {"columns": list(rows[0]) if rows else [], "row_count": len(rows), "sample": rows[:3]}


def _mapped(row, mapping, key):
    return row.get(mapping.get(key, key))


def _period(value, grain):
    raw = _text(value)
    if grain == "month":
        if len(raw) < 7:
            raise ValueError(f"Expected YYYY-MM period, got {raw!r}")
        date.fromisoformat(raw[:7] + "-01")
        return raw[:7]
    if grain == "day":
        return date.fromisoformat(raw[:10]).isoformat()
    raise ValueError("grain must be month or day")


def _combine_search_rows(prepared):
    grouped = {}
    for period, page_id, keyword_id, values in prepared:
        key = (period, page_id, keyword_id)
        if key not in grouped:
            grouped[key] = [*values, (values[2] or 0) * (values[1] or 0),
                            (values[1] or 0) if values[2] is not None else 0,
                            1 if values[2] is not None else 0, values[2] or 0]
            continue
        item = grouped[key]
        item[0] = None if item[0] is None and values[0] is None else (item[0] or 0) + (values[0] or 0)
        item[1] = None if item[1] is None and values[1] is None else (item[1] or 0) + (values[1] or 0)
        if values[2] is not None:
            item[3] += values[2] * (values[1] or 0)
            item[4] += values[1] or 0
            item[5] += 1
            item[6] += values[2]
            item[2] = item[3] / item[4] if item[4] else item[6] / item[5]
    return [(period, page_id, keyword_id, tuple(grouped[(period, page_id, keyword_id)][:3]))
            for period, page_id, keyword_id in grouped]


def import_records(site_id, rows, *, dataset, source_name, provider="file", geo="GLOBAL",
                   grain="month", mapping=None, default_period=None, coverage="partial",
                   fingerprint=None, actor="user"):
    if dataset not in DATASETS:
        raise ValueError("Unsupported dataset")
    if coverage not in {"partial", "complete"}:
        raise ValueError("coverage must be partial or complete")
    geo = normalize_geo(geo)
    mapping = mapping or {}
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("mapping must map canonical field names to column names")
    if not source_name.strip():
        raise ValueError("source_name is required")
    info = site_info(site_id)
    prepared = []
    with site_db(site_id) as db:
        source_id = ensure_source(db, source_name, provider)
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ValueError(f"Row {index} must be an object")
            aliases = {"page_url": ("page", "url"), "keyword": ("query",),
                       "period": ("date", "month", "day"), "volume": ("search_volume",)}
            get = lambda key: _mapped(row, mapping, key) if key in mapping or key in row else next(
                (row[alias] for alias in aliases.get(key, ()) if alias in row), None)
            try:
                period = _period(get("period") or default_period, grain) if dataset != "inquiry" else None
                page_id = ensure_page(db, get("page_url"), info["primary_domain"],
                                      page_type=_text(get("page_type")) or None,
                                      language=_text(get("language")) or None) if dataset in {"search_page", "search_page_keyword", "inquiry", "custom_metric"} else None
                keyword_id = ensure_keyword(db, get("keyword")) if dataset in {"search_page_keyword", "keyword_demand", "custom_metric"} else None
                if dataset in {"search_page", "search_page_keyword"}:
                    if page_id is None or (dataset == "search_page_keyword" and keyword_id is None):
                        raise ValueError("page_url and keyword are required for this dataset")
                    values = (_number(get("clicks"), True), _number(get("impressions"), True), _number(get("position")))
                    if all(x is None for x in values):
                        raise ValueError("At least one search metric is required")
                    prepared.append((period, page_id, keyword_id, values))
                elif dataset == "keyword_demand":
                    if keyword_id is None:
                        raise ValueError("keyword is required")
                    prepared.append((period, keyword_id, (_number(get("volume"), True), _number(get("difficulty")), _number(get("cpc")))))
                elif dataset == "inquiry":
                    submitted = _text(get("submitted_on"))
                    if submitted:
                        date.fromisoformat(submitted[:10])
                    external = _text(get("external_id")) or hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
                    landing = ensure_page(db, get("landing_url"), info["primary_domain"])
                    submit = ensure_page(db, get("submit_url"), info["primary_domain"])
                    prepared.append((external, submitted or None, landing, submit, _text(get("landing_url")),
                                     _text(get("submit_url")), _text(get("quality")) or None,
                                     _text(get("attribution")) or None))
                else:
                    scope = _text(get("entity_scope")) or ("page" if page_id else "keyword")
                    entity_id = page_id if scope == "page" else keyword_id
                    metric_key = _text(get("metric_key"))
                    if entity_id is None or not db.execute("SELECT 1 FROM metric_definitions WHERE metric_key=? AND entity_scope=?", (metric_key, scope)).fetchone():
                        raise ValueError("Registered metric_key and matching entity are required")
                    prepared.append((period, scope, entity_id, metric_key, _number(get("value")), _text(get("value_text")) or None))
            except (ValueError, TypeError, KeyError) as exc:
                db.rollback()
                raise ValueError(f"Row {index}: {exc}") from exc
        if not prepared:
            raise ValueError("No data rows to import")
        if dataset in {"search_page", "search_page_keyword"}:
            prepared = _combine_search_rows(prepared)
        fingerprint = fingerprint or hashlib.sha256(json.dumps({"dataset": dataset, "geo": geo, "grain": grain,
            "source": source_name, "mapping": mapping, "coverage": coverage,
            "default_period": default_period, "rows": rows}, sort_keys=True, default=str).encode()).hexdigest()
        existing = db.execute("SELECT run_id,row_count FROM import_runs WHERE source_id=? AND dataset=? AND fingerprint=? AND status='complete'",
                              (source_id, dataset, fingerprint)).fetchone()
        if existing:
            db.rollback()
            return {"run_id": existing[0], "row_count": existing[1], "duplicate": True}
        periods = sorted({(item[1] or "")[:7] for item in prepared if item[1]}) if dataset == "inquiry" else sorted({item[0] for item in prepared if item[0]})
        run_id = db.execute("INSERT INTO import_runs(source_id,dataset,grain,geo,period_start,period_end,fingerprint,status,row_count,coverage,started_at) VALUES(?,?,?,?,?,?,?,'running',0,?,?)",
            (source_id, dataset, grain, geo, periods[0] if periods else None, periods[-1] if periods else None, fingerprint, coverage, utc_now())).lastrowid
        table = {"search_page": "search_page_metrics", "search_page_keyword": "search_page_keyword_metrics",
                 "keyword_demand": "keyword_demand", "custom_metric": "custom_metrics"}.get(dataset)
        if coverage == "complete" and table and periods:
            field = "snapshot_month" if dataset == "keyword_demand" else "period"
            marks = ",".join("?" for _ in periods)
            db.execute(f"DELETE FROM {table} WHERE source_id=? AND geo=? AND {field} IN ({marks})" +
                       (" AND grain=?" if dataset != "keyword_demand" else ""),
                       (source_id, geo, *periods, *((grain,) if dataset != "keyword_demand" else ())))
        inserted = 0
        for item in prepared:
            if dataset == "search_page":
                period, pid, _, values = item
                db.execute("INSERT INTO search_page_metrics VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(page_id,period,grain,geo,source_id) DO UPDATE SET clicks=excluded.clicks,impressions=excluded.impressions,position=excluded.position,run_id=excluded.run_id",
                           (pid, period, grain, geo, source_id, *values, run_id))
            elif dataset == "search_page_keyword":
                period, pid, kid, values = item
                db.execute("INSERT INTO search_page_keyword_metrics VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(page_id,keyword_id,period,grain,geo,source_id) DO UPDATE SET clicks=excluded.clicks,impressions=excluded.impressions,position=excluded.position,run_id=excluded.run_id",
                           (pid, kid, period, grain, geo, source_id, *values, run_id))
            elif dataset == "keyword_demand":
                period, kid, values = item
                db.execute("INSERT INTO keyword_demand VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(keyword_id,snapshot_month,geo,source_id) DO UPDATE SET volume=excluded.volume,difficulty=excluded.difficulty,cpc=excluded.cpc,run_id=excluded.run_id",
                           (kid, period, geo, source_id, *values, run_id))
            elif dataset == "inquiry":
                external, submitted, landing, submit, landing_url, submit_url, quality, attribution = item
                cursor = db.execute("INSERT OR IGNORE INTO inquiries(source_id,external_id,submitted_on,landing_page_id,submit_page_id,landing_url,submit_url,quality,attribution,geo,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                    (source_id, external, submitted, landing, submit, landing_url, submit_url, quality, attribution, geo, run_id))
                inserted += cursor.rowcount
                continue
            else:
                period, scope, entity_id, metric_key, value, text = item
                db.execute("INSERT INTO custom_metrics VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(entity_scope,entity_id,metric_key,period,grain,geo,source_id) DO UPDATE SET value_number=excluded.value_number,value_text=excluded.value_text,run_id=excluded.run_id",
                           (scope, entity_id, metric_key, period, grain, geo, source_id, value, text, run_id))
            inserted += 1
        db.execute("UPDATE import_runs SET status='complete',row_count=?,finished_at=? WHERE run_id=?", (inserted, utc_now(), run_id))
        audit(db, actor, "import", {"run_id": run_id, "dataset": dataset, "source": source_name, "rows": inserted, "geo": geo})
        db.commit()
        return {"run_id": run_id, "row_count": inserted, "duplicate": False}


def import_file(site_id, blob, filename, *, dataset, source_name, provider="file", geo="GLOBAL",
                grain="month", mapping=None, default_period=None, coverage="partial", actor="user"):
    if filename.lower().endswith(".json"):
        raw = json.loads(blob)
        if isinstance(raw, dict) and "request" in raw:
            first = raw["request"].get("startDate", "")
            last = raw["request"].get("endDate", "")
            if first and last and ((grain == "month" and first[:7] != last[:7]) or
                                   (grain == "day" and first[:10] != last[:10])):
                raise ValueError("GSC JSON spans multiple periods; split the export by month or day")
    rows = read_file(blob, filename)
    fingerprint = hashlib.sha256(blob + json.dumps({"dataset": dataset, "source": source_name, "geo": geo,
        "grain": grain, "mapping": mapping or {}, "period": default_period, "coverage": coverage}, sort_keys=True).encode()).hexdigest()
    return import_records(site_id, rows, dataset=dataset, source_name=source_name, provider=provider,
                          geo=geo, grain=grain, mapping=mapping, default_period=default_period,
                          coverage=coverage, fingerprint=fingerprint, actor=actor)
