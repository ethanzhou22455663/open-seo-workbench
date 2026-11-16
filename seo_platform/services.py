"""Shared application services used by REST, CLI, and MCP."""

import json
import re
from datetime import date, timedelta

from .imports import normalize_geo
from .storage import audit, classify_page, config_value, list_sites, set_config, site_db, site_info, utc_now

SOURCE_TABLES = {"search_page": "search_page_metrics", "search_page_keyword": "search_page_keyword_metrics",
                 "keyword_demand": "keyword_demand", "inquiry": "inquiries"}


def site_settings(site_id):
    with site_db(site_id) as db:
        return {row["key"]: json.loads(row["value_json"]) for row in db.execute("SELECT * FROM site_config")}


def update_settings(site_id, updates, actor="user"):
    allowed = {"default_geo", "default_demand_geo", "language_filter", "page_type_patterns", "intent_terms", "product_query_exclude_terms", "rules"}
    if not isinstance(updates, dict) or set(updates) - allowed:
        raise ValueError("Unknown setting")
    if "default_geo" in updates:
        updates["default_geo"] = normalize_geo(updates["default_geo"])
    if "default_demand_geo" in updates:
        updates["default_demand_geo"] = normalize_geo(updates["default_demand_geo"])
    if "page_type_patterns" in updates:
        patterns = updates["page_type_patterns"]
        if not isinstance(patterns, list) or len(patterns) > 100:
            raise ValueError("page_type_patterns must be a list of at most 100 rules")
        for rule in patterns:
            if not isinstance(rule, dict) or set(rule) != {"pattern", "type"}:
                raise ValueError("Each page type rule needs pattern and type")
            re.compile(rule["pattern"])
    for term_key in ("intent_terms", "product_query_exclude_terms"):
        if term_key in updates and (not isinstance(updates[term_key], list) or
                                    not all(isinstance(x, str) for x in updates[term_key])):
            raise ValueError(f"{term_key} must be a string list")
    if "rules" in updates:
        rules = updates["rules"]
        required = {"primary_min_impressions", "min_impressions", "min_volume", "product_max_clicks",
                    "low_ctr", "min_click_loss", "lead_lookback_days",
                    "traffic_high_product", "traffic_high_category", "traffic_high_blog", "traffic_high_other",
                    "lead_high_count", "click_slight_delta", "click_strong_delta",
                    "rank_slight_delta", "rank_strong_delta"}
        if not isinstance(rules, dict) or set(rules) != required or not all(isinstance(v, (int, float)) and v >= 0 for v in rules.values()):
            raise ValueError("rules must contain non-negative numeric thresholds")
        if not 0 <= rules["low_ctr"] <= 1 or not 1 <= rules["lead_lookback_days"] <= 730:
            raise ValueError("CTR or inquiry lookback is out of range")
        if rules["click_strong_delta"] < rules["click_slight_delta"] or rules["rank_strong_delta"] < rules["rank_slight_delta"]:
            raise ValueError("Strong change thresholds must exceed slight change thresholds")
    with site_db(site_id) as db:
        with db:
            for key, value in updates.items():
                set_config(db, key, value)
            if "rules" in updates:
                version = config_value(db, "rules_version", 1) + 1
                set_config(db, "rules_version", version)
                db.execute("INSERT INTO rule_versions VALUES(?,?,?)",
                           (version, json.dumps(updates["rules"], ensure_ascii=False), utc_now()))
            if "page_type_patterns" in updates:
                for row in db.execute("SELECT page_id,canonical_url FROM pages WHERE page_type_origin='auto'").fetchall():
                    db.execute("UPDATE pages SET page_type=? WHERE page_id=?",
                               (classify_page(row["canonical_url"], updates["page_type_patterns"]), row["page_id"]))
            audit(db, actor, "update_settings", updates)
    return site_settings(site_id)


def list_sources(site_id):
    with site_db(site_id) as db:
        sources = [dict(row) for row in db.execute("SELECT source_id,name,provider,kind,created_at FROM data_sources ORDER BY name")]
        availability = [dict(row) for row in db.execute("""
            SELECT 'search_page' dataset,source_id,geo,COUNT(*) rows FROM search_page_metrics GROUP BY source_id,geo
            UNION ALL SELECT 'search_page_keyword',source_id,geo,COUNT(*) FROM search_page_keyword_metrics GROUP BY source_id,geo
            UNION ALL SELECT 'keyword_demand',source_id,geo,COUNT(*) FROM keyword_demand GROUP BY source_id,geo
            UNION ALL SELECT 'inquiry',source_id,geo,COUNT(*) FROM inquiries GROUP BY source_id,geo""")]
        preferences = [dict(row) for row in db.execute("SELECT * FROM source_preferences")]
    return {"sources": sources, "availability": availability, "preferences": preferences}


def set_source_preference(site_id, metric_key, geo, names, actor="user"):
    geo = normalize_geo(geo)
    if not isinstance(names, list) or not names or len(names) > 20 or not all(isinstance(x, str) for x in names):
        raise ValueError("priority must be a list of source names")
    with site_db(site_id) as db:
        if not db.execute("SELECT 1 FROM metric_definitions WHERE metric_key=?", (metric_key,)).fetchone():
            raise ValueError("Unknown metric")
        known = {r[0] for r in db.execute("SELECT name FROM data_sources")}
        if set(names) - known:
            raise ValueError("Unknown source name")
        with db:
            db.execute("INSERT INTO source_preferences VALUES(?,?,?) ON CONFLICT(metric_key,geo) DO UPDATE SET priority_json=excluded.priority_json",
                       (metric_key, geo, json.dumps(names)))
            audit(db, actor, "source_preference", {"metric_key": metric_key, "geo": geo, "priority": names})
    return {"metric_key": metric_key, "geo": geo, "priority": names}


def metric_definitions(site_id):
    with site_db(site_id) as db:
        return [dict(row) for row in db.execute("SELECT * FROM metric_definitions ORDER BY builtin DESC,metric_key")]


def add_metric_definition(site_id, metric_key, label, entity_scope, unit, aggregation="sum", description="", actor="user"):
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", metric_key):
        raise ValueError("metric_key must be snake_case")
    if entity_scope not in {"page", "keyword"} or aggregation not in {"sum", "latest", "mean"}:
        raise ValueError("Invalid metric scope or aggregation")
    with site_db(site_id) as db:
        with db:
            db.execute("INSERT INTO metric_definitions VALUES(?,?,?,?,?,?,0)",
                       (metric_key, label, entity_scope, unit, aggregation, description))
            audit(db, actor, "add_metric", {"metric_key": metric_key, "scope": entity_scope})
    return {"metric_key": metric_key, "label": label, "entity_scope": entity_scope, "unit": unit,
            "aggregation": aggregation, "description": description}


def _preferred_source(db, dataset, geo, metric_key, requested=None):
    table = SOURCE_TABLES[dataset]
    candidates = [dict(r) for r in db.execute(f"SELECT DISTINCT s.source_id,s.name,s.provider FROM {table} m JOIN data_sources s ON s.source_id=m.source_id WHERE m.geo=? ORDER BY s.source_id", (geo,))]
    if requested:
        match = next((item for item in candidates if item["name"] == requested), None)
        if match is None:
            raise ValueError(f"Source {requested!r} has no {dataset} data in {geo}")
        return match
    row = db.execute("SELECT priority_json FROM source_preferences WHERE metric_key=? AND geo=?", (metric_key, geo)).fetchone()
    if row:
        for name in json.loads(row[0]):
            match = next((item for item in candidates if item["name"] == name), None)
            if match:
                return match
    return candidates[0] if candidates else None


def _latest_month(db, geo, source_id=None):
    if source_id:
        row = db.execute("SELECT MAX(period) FROM search_page_metrics WHERE geo=? AND grain='month' AND source_id=?", (geo, source_id)).fetchone()
    else:
        row = db.execute("SELECT MAX(period) FROM (SELECT period FROM search_page_metrics WHERE geo=? AND grain='month' UNION ALL SELECT period FROM search_page_keyword_metrics WHERE geo=? AND grain='month')", (geo, geo)).fetchone()
    return row[0] if row else None


def _query_source(db, geo, requested=None):
    return _preferred_source(db, "search_page_keyword", geo, "search_position", requested)


def _demand_source(db, geo, requested=None):
    return _preferred_source(db, "keyword_demand", geo, "keyword_volume", requested)


def _inquiry_source(db, geo, requested=None):
    return _preferred_source(db, "inquiry", geo, "normal_inquiries", requested)


def _page_metrics(db, month, geo, source):
    if not source:
        return {}
    return {row["page_id"]: dict(row) for row in db.execute("SELECT page_id,clicks,impressions,position FROM search_page_metrics WHERE period=? AND grain='month' AND geo=? AND source_id=?",
                                                           (month, geo, source["source_id"]))}


def _inquiry_counts(db, month, geo, source):
    if not source:
        return {}
    return {r["landing_page_id"]: r["n"] for r in db.execute("SELECT landing_page_id,COUNT(*) n FROM inquiries WHERE source_id=? AND geo=? AND quality='NORMAL' AND substr(submitted_on,1,7)=? AND landing_page_id IS NOT NULL GROUP BY landing_page_id",
                                                                   (source["source_id"], geo, month))}


def _keyword_rows(db, page_id, month, geo, source):
    if not source:
        return []
    return [dict(row) for row in db.execute("SELECT m.keyword_id,k.query,m.clicks,m.impressions,m.position FROM search_page_keyword_metrics m JOIN keywords k USING(keyword_id) WHERE m.page_id=? AND m.period=? AND m.grain='month' AND m.geo=? AND m.source_id=? ORDER BY COALESCE(m.clicks,0) DESC,COALESCE(m.impressions,0) DESC,k.keyword_id",
                                           (page_id, month, geo, source["source_id"]))]


def _demand(db, keyword_id, geo, source):
    if not source:
        return None
    row = db.execute("SELECT snapshot_month,volume,difficulty,cpc FROM keyword_demand WHERE keyword_id=? AND geo=? AND source_id=? ORDER BY snapshot_month DESC LIMIT 1",
                     (keyword_id, geo, source["source_id"])).fetchone()
    return dict(row) if row else None


def list_pages(site_id, month=None, geo=None, search="", page_type=None, limit=100, offset=0,
               search_source=None, inquiry_source=None):
    info = site_info(site_id)
    with site_db(site_id) as db:
        geo = normalize_geo(geo or config_value(db, "default_geo", "GLOBAL"))
        perf = _preferred_source(db, "search_page", geo, "search_clicks", search_source)
        leads = _inquiry_source(db, geo, inquiry_source)
        month = month or _latest_month(db, geo, perf["source_id"] if perf else None)
        metrics = _page_metrics(db, month, geo, perf) if month else {}
        inquiries = _inquiry_counts(db, month, geo, leads) if month else {}
        clauses = []
        params = []
        if search:
            clauses.append("canonical_url LIKE ?")
            params.append("%" + search + "%")
        if page_type:
            clauses.append("page_type=?")
            params.append(page_type)
        else:
            clauses.append("page_type<>'asset'")
        language = config_value(db, "language_filter")
        if language:
            clauses.append("language=?")
            params.append(language)
        sql = "SELECT page_id,canonical_url,page_type,language,business_priority FROM pages"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = []
        for page in db.execute(sql, params):
            row = dict(page)
            metric = metrics.get(row["page_id"])
            row.update(clicks=metric["clicks"] if metric else None,
                       impressions=metric["impressions"] if metric else None,
                       position=metric["position"] if metric else None,
                       normal_inquiries=inquiries.get(row["page_id"], 0) if leads else None,
                       has_search_data=metric is not None)
            rows.append(row)
        rows.sort(key=lambda x: (x["clicks"] is None, -(x["clicks"] or 0), -(x["impressions"] or 0), x["page_id"]))
        total = len(rows)
        eligible = {row["page_id"] for row in rows}
        rows = rows[max(offset, 0):max(offset, 0) + min(max(limit, 1), 500)]
        return {"site": {k: info[k] for k in ("site_id", "name", "primary_domain", "timezone")},
                "month": month, "geo": geo, "search_source": perf, "inquiry_source": leads,
                "total": total, "pages": rows,
                "summary": {"observed_pages": len(eligible & metrics.keys()),
                            "search_clicks": sum((x["clicks"] or 0) for pid,x in metrics.items() if pid in eligible) if perf else None,
                            "search_impressions": sum((x["impressions"] or 0) for pid,x in metrics.items() if pid in eligible) if perf else None,
                            "normal_inquiries": sum(n for pid,n in inquiries.items() if pid in eligible) if leads else None}}


def page_detail(site_id, page_id, month=None, geo=None, search_source=None, query_source=None,
                demand_source=None, demand_geo=None, inquiry_source=None, limit=100, offset=0):
    with site_db(site_id) as db:
        page = db.execute("SELECT * FROM pages WHERE page_id=?", (page_id,)).fetchone()
        if not page:
            raise ValueError("Unknown page_id")
        geo = normalize_geo(geo or config_value(db, "default_geo", "GLOBAL"))
        demand_geo = normalize_geo(demand_geo or config_value(db, "default_demand_geo", geo))
        perf = _preferred_source(db, "search_page", geo, "search_clicks", search_source)
        queries = _query_source(db, geo, query_source)
        demand = _demand_source(db, demand_geo, demand_source)
        leads = _inquiry_source(db, geo, inquiry_source)
        month = month or _latest_month(db, geo, perf["source_id"] if perf else None)
        current = _page_metrics(db, month, geo, perf).get(page_id) if month else None
        previous = None
        if month:
            previous_month = (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")
            previous = _page_metrics(db, previous_month, geo, perf).get(page_id)
        words = _keyword_rows(db, page_id, month, geo, queries) if month else []
        previous_words = {item["keyword_id"]: item for item in _keyword_rows(db, page_id, previous_month, geo, queries)} if month else {}
        for word in words:
            word["ctr"] = word["clicks"] / word["impressions"] if word["clicks"] is not None and word["impressions"] else None
            word["demand"] = _demand(db, word["keyword_id"], demand_geo, demand)
            prior = previous_words.get(word["keyword_id"])
            word["previous"] = {key: prior[key] for key in ("clicks", "impressions", "position")} if prior else None
            word["change"] = {key: word[key] - prior[key] if prior and word[key] is not None and prior[key] is not None else None
                              for key in ("clicks", "impressions", "position")}
        aliases = [r[0] for r in db.execute("SELECT alias_url FROM page_aliases WHERE page_id=?", (page_id,))]
        lead_months = [dict(r) for r in db.execute("SELECT substr(submitted_on,1,7) month,COUNT(*) count FROM inquiries WHERE landing_page_id=? AND source_id=? AND geo=? AND quality='NORMAL' GROUP BY substr(submitted_on,1,7) ORDER BY month DESC",
                                                   (page_id, leads["source_id"], geo))] if leads else []
        return {"page": dict(page), "aliases": aliases, "month": month, "geo": geo, "demand_geo": demand_geo,
                "search_source": perf, "query_source": queries, "demand_source": demand, "inquiry_source": leads,
                "metrics": current, "previous_metrics": previous, "keyword_count": len(words),
                "keywords": words[max(offset, 0):max(offset, 0) + min(max(limit, 1), 500)],
                "inquiries_by_month": lead_months,
                "visible_query_clicks": sum(w["clicks"] or 0 for w in words) if queries else None,
                "hidden_query_clicks": max(0, (current["clicks"] or 0) - sum(w["clicks"] or 0 for w in words))
                if current and current["clicks"] is not None and queries and perf and perf["source_id"] == queries["source_id"] else None}


def import_runs(site_id, limit=50):
    with site_db(site_id) as db:
        return [dict(r) for r in db.execute("SELECT r.*,s.name source_name,s.provider FROM import_runs r JOIN data_sources s USING(source_id) ORDER BY run_id DESC LIMIT ?", (min(max(limit, 1), 200),))]


def metric_series(site_id, metric_key, entity_id, geo=None, source_name=None, limit=36, keyword_id=None):
    with site_db(site_id) as db:
        definition = db.execute("SELECT * FROM metric_definitions WHERE metric_key=?", (metric_key,)).fetchone()
        if not definition:
            raise ValueError("Unknown metric")
        geo = normalize_geo(geo or config_value(db, "default_geo", "GLOBAL"))
        if metric_key == "search_position":
            if keyword_id is None:
                raise ValueError("keyword_id is required for a page × keyword rank series")
            source = _query_source(db, geo, source_name)
            rows = [dict(r) for r in db.execute("SELECT period,position value,impressions FROM search_page_keyword_metrics WHERE page_id=? AND keyword_id=? AND geo=? AND grain='month' AND source_id=? ORDER BY period DESC LIMIT ?",
                                              (entity_id, keyword_id, geo, source["source_id"], limit))] if source else []
        elif metric_key in {"search_clicks", "search_impressions"}:
            source = _preferred_source(db, "search_page", geo, "search_clicks", source_name)
            field = {"search_clicks": "clicks", "search_impressions": "impressions"}[metric_key]
            rows = [dict(r) for r in db.execute(f"SELECT period,{field} value FROM search_page_metrics WHERE page_id=? AND geo=? AND grain='month' AND source_id=? ORDER BY period DESC LIMIT ?",
                                              (entity_id, geo, source["source_id"], limit))] if source else []
        elif metric_key == "keyword_volume":
            source = _demand_source(db, geo, source_name)
            rows = [dict(r) for r in db.execute("SELECT snapshot_month period,volume value FROM keyword_demand WHERE keyword_id=? AND geo=? AND source_id=? ORDER BY snapshot_month DESC LIMIT ?",
                                              (entity_id, geo, source["source_id"], limit))] if source else []
        elif metric_key == "normal_inquiries":
            source = _inquiry_source(db, geo, source_name)
            rows = [dict(r) for r in db.execute("SELECT substr(submitted_on,1,7) period,COUNT(*) value FROM inquiries WHERE landing_page_id=? AND source_id=? AND geo=? AND quality='NORMAL' GROUP BY substr(submitted_on,1,7) ORDER BY period DESC LIMIT ?",
                                              (entity_id, source["source_id"], geo, limit))] if source else []
        else:
            available = [dict(r) for r in db.execute("SELECT DISTINCT s.source_id,s.name,s.provider FROM custom_metrics m JOIN data_sources s USING(source_id) WHERE m.metric_key=? AND m.geo=? ORDER BY s.source_id", (metric_key, geo))]
            if source_name:
                source = next((x for x in available if x["name"] == source_name), None)
                if source is None: raise ValueError("Source has no data for this metric and geography")
            else:
                preferred = db.execute("SELECT priority_json FROM source_preferences WHERE metric_key=? AND geo=?", (metric_key, geo)).fetchone()
                priority = json.loads(preferred[0]) if preferred else []
                source = next((x for name in priority for x in available if x["name"] == name), None) or (available[0] if available else None)
            rows = [dict(r) for r in db.execute("SELECT period,value_number value,value_text FROM custom_metrics WHERE entity_scope=? AND entity_id=? AND metric_key=? AND geo=? AND source_id=? ORDER BY period DESC LIMIT ?",
                                              (definition["entity_scope"], entity_id, metric_key, geo, source["source_id"], limit))] if source else []
        return {"metric": dict(definition), "geo": geo, "source": source, "values": rows}


def add_task(site_id, keyword_id=None, page_id=None, proposed_page_type=None, note="", actor="user"):
    with site_db(site_id) as db:
        with db:
            if keyword_id and not db.execute("SELECT 1 FROM keywords WHERE keyword_id=?", (keyword_id,)).fetchone():
                raise ValueError("Unknown keyword_id")
            if page_id and not db.execute("SELECT 1 FROM pages WHERE page_id=?", (page_id,)).fetchone():
                raise ValueError("Unknown page_id")
            task_id = db.execute("INSERT INTO seo_tasks(keyword_id,page_id,proposed_page_type,note,created_at) VALUES(?,?,?,?,datetime('now'))",
                                 (keyword_id, page_id, proposed_page_type, note)).lastrowid
            audit(db, actor, "add_task", {"task_id": task_id, "page_id": page_id, "keyword_id": keyword_id})
        return {"task_id": task_id}


def list_tasks(site_id):
    with site_db(site_id) as db:
        return [dict(r) for r in db.execute("SELECT t.*,k.query,p.canonical_url FROM seo_tasks t LEFT JOIN keywords k USING(keyword_id) LEFT JOIN pages p USING(page_id) ORDER BY task_id DESC LIMIT 200")]
