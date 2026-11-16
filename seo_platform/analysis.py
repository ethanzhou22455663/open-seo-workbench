"""Evidence-gated opportunities. Missing inputs never become zero observations."""

import re
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .imports import normalize_geo
from .services import _demand_source, _inquiry_source, _latest_month, _preferred_source, _query_source
from .storage import config_value, site_db, site_info

PRESETS = (
    ("recover", "挽回有询盘页面", "high"),
    ("product_win", "产品页排名机会", "high"),
    ("buyer_decay", "采购内容持续衰退", "high"),
    ("low_ctr", "高曝光低点击", "medium"),
    ("page_two", "第二页待突破", "medium"),
    ("traffic_no_leads", "有流量无记录询盘", "normal"),
    ("multi_url", "同词多页", "medium"),
    ("zero_click", "零点击产品词", "medium"),
)
LEVEL_ORDER = {"high": 0, "medium": 1, "normal": 2}
FACETS = {
    "traffic_tier": {"high", "low", "none", "unknown"},
    "lead_tier": {"high", "low", "none", "unknown"},
    "click_trend": {"strong_up", "slight_up", "stable", "slight_down", "strong_down", "unknown"},
    "rank_trend": {"strong_up", "slight_up", "stable", "slight_down", "strong_down", "unknown"},
    "rank_band": {"top5", "first_page", "second_page", "later", "unknown"},
}


def validate_facets(facets):
    if not isinstance(facets, dict) or set(facets) - set(FACETS):
        raise ValueError("Unknown facet group")
    for key, values in facets.items():
        if not isinstance(values, list) or set(values) - FACETS[key]:
            raise ValueError(f"Invalid values for {key}")
    return facets


def _trend(current, previous, slight, strong, lower_is_better=False):
    if current is None or previous is None:
        return "unknown"
    change = (previous - current) if lower_is_better else (current - previous)
    if change >= strong: return "strong_up"
    if change >= slight: return "slight_up"
    if change <= -strong: return "strong_down"
    if change <= -slight: return "slight_down"
    return "stable"


def _previous_month(month):
    return (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")


def _terms(text):
    result = set()
    for word in re.findall(r"[\w]+", str(text or "").casefold(), re.UNICODE):
        if word.endswith("ies") and len(word) > 5: word = word[:-3] + "y"
        elif word.endswith("s") and len(word) > 4: word = word[:-1]
        result.add(word)
    return result


def analyse(site_id, month=None, geo=None, demand_geo=None, search_source=None,
            query_source=None, demand_source=None, inquiry_source=None,
            preset=None, page_type=None, search="", facets=None, limit=100, offset=0):
    if preset and preset not in {item[0] for item in PRESETS}:
        raise ValueError("Unknown preset")
    facets = validate_facets(facets or {})
    with site_db(site_id) as db:
        geo = normalize_geo(geo or config_value(db, "default_geo", "GLOBAL"))
        demand_geo = normalize_geo(demand_geo or config_value(db, "default_demand_geo", geo))
        perf = _preferred_source(db, "search_page", geo, "search_clicks", search_source)
        queries = _query_source(db, geo, query_source)
        demand = _demand_source(db, demand_geo, demand_source)
        leads = _inquiry_source(db, geo, inquiry_source)
        month = month or _latest_month(db, geo, perf["source_id"] if perf else None)
        if not month:
            return {"month": None, "geo": geo, "demand_geo": demand_geo, "total": 0, "rows": [],
                    "sources": {"search": perf, "query": queries, "demand": demand, "inquiry": leads}}
        partial_month = month >= datetime.now(ZoneInfo(site_info(site_id)["timezone"])).strftime("%Y-%m")
        rules = config_value(db, "rules", {})
        rule_version = config_value(db, "rules_version", 1)
        min_imp = rules.get("min_impressions", 50)
        min_primary = rules.get("primary_min_impressions", 15)
        min_vol = rules.get("min_volume", 50)
        product_max_clicks = rules.get("product_max_clicks", 5)
        low_ctr = rules.get("low_ctr", 0.01)
        min_loss = rules.get("min_click_loss", 2)
        lead_days = rules.get("lead_lookback_days", 365)
        intent_terms = set().union(*(_terms(x) for x in config_value(db, "intent_terms", [])))
        product_exclude = set().union(*(_terms(x) for x in config_value(db, "product_query_exclude_terms", [])))
        prev = _previous_month(month)
        older = _previous_month(prev)
        language = config_value(db, "language_filter")
        page_rows = [dict(r) for r in db.execute("SELECT page_id,canonical_url,page_type,business_priority FROM pages WHERE page_type<>'asset'" +
                                                (" AND language=?" if language else ""), (language,) if language else ())]
        page_metrics = defaultdict(dict)
        if perf:
            for row in db.execute("SELECT page_id,period,clicks,impressions,position FROM search_page_metrics WHERE source_id=? AND geo=? AND grain='month' AND period IN (?,?,?)",
                                  (perf["source_id"], geo, month, prev, older)):
                page_metrics[row["page_id"]][row["period"]] = dict(row)
        words = defaultdict(list)
        prev_words = {}
        word_pages = defaultdict(set)
        if queries:
            for row in db.execute("SELECT m.page_id,m.keyword_id,k.query,m.period,m.clicks,m.impressions,m.position FROM search_page_keyword_metrics m JOIN keywords k USING(keyword_id) WHERE m.source_id=? AND m.geo=? AND m.grain='month' AND m.period IN (?,?)",
                                  (queries["source_id"], geo, month, prev)):
                data = dict(row)
                if row["period"] == month:
                    words[row["page_id"]].append(data)
                    if (row["impressions"] or 0) >= min_imp:
                        word_pages[row["keyword_id"]].add(row["page_id"])
                else:
                    prev_words[(row["page_id"], row["keyword_id"])] = data
        volumes = {}
        if demand:
            for row in db.execute("SELECT keyword_id,volume,snapshot_month FROM keyword_demand WHERE source_id=? AND geo=? ORDER BY snapshot_month DESC",
                                  (demand["source_id"], demand_geo)):
                volumes.setdefault(row["keyword_id"], dict(row))
        lead_count = defaultdict(int)
        if leads:
            last = date.fromisoformat(month + "-01")
            next_month = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
            end = next_month - timedelta(days=1)
            start = end - timedelta(days=lead_days - 1)
            for row in db.execute("SELECT landing_page_id,COUNT(*) n FROM inquiries WHERE source_id=? AND geo=? AND quality='NORMAL' AND submitted_on BETWEEN ? AND ? GROUP BY landing_page_id",
                                  (leads["source_id"], geo, start.isoformat(), end.isoformat())):
                lead_count[row["landing_page_id"]] = row["n"]
        result = []
        for page in page_rows:
            pid = page["page_id"]
            current = page_metrics[pid].get(month)
            previous = page_metrics[pid].get(prev)
            prior = page_metrics[pid].get(older)
            path_terms = _terms(page["canonical_url"].split("/", 3)[-1])
            path_buyer = bool(path_terms & intent_terms)
            candidates = [w for w in words[pid] if (w["impressions"] or 0) >= min_primary]
            if page["page_type"] in {"product", "category"}:
                candidates = [w for w in candidates if not (_terms(w["query"]) & product_exclude)
                              and (len(path_terms & _terms(w["query"])) >= 2 or
                                   (len(_terms(w["query"])) <= 2 and len(path_terms & _terms(w["query"])) >= 1))]
            def word_score(word):
                query_terms = _terms(word["query"])
                overlap = len(path_terms & query_terms)
                demand_value = (volumes.get(word["keyword_id"]) or {}).get("volume") or 0
                value = (word["impressions"] or 0) * math.log2(demand_value + 2) + (word["clicks"] or 0) * 100
                if page["page_type"] == "blog" and path_buyer:
                    return (bool(query_terms & intent_terms) and overlap >= 2, value, overlap)
                if page["page_type"] in {"product", "category"}:
                    return (overlap >= 2, value, overlap)
                return (word["clicks"] or 0, word["impressions"] or 0)
            page_words = sorted(candidates, key=word_score, reverse=True)
            primary = page_words[0] if page_words else None
            previous_word = prev_words.get((pid, primary["keyword_id"])) if primary else None
            volume = volumes.get(primary["keyword_id"]) if primary else None
            clicks = current["clicks"] if current else None
            impressions = current["impressions"] if current else None
            query_clicks = primary["clicks"] if primary else None
            query_imp = primary["impressions"] if primary else None
            rank = primary["position"] if primary else None
            lead = lead_count[pid] if leads else None
            buyer = bool(primary and _terms(primary["query"]) & intent_terms)
            traffic_cutoff = rules.get("traffic_high_" + page["page_type"], rules.get("traffic_high_other", 3))
            traffic_tier = "unknown" if clicks is None else "none" if clicks == 0 else "high" if clicks >= traffic_cutoff else "low"
            lead_tier = "unknown" if lead is None else "none" if lead == 0 else "high" if lead >= rules.get("lead_high_count", 2) else "low"
            click_trend = _trend(clicks if not partial_month else None, previous["clicks"] if previous else None,
                                 rules.get("click_slight_delta", 2), rules.get("click_strong_delta", 6))
            rank_band = "unknown" if rank is None else "top5" if rank <= 5 else "first_page" if rank <= 10 else "second_page" if rank <= 20 else "later"
            rank_trend = _trend(rank if not partial_month else None, previous_word["position"] if previous_word else None,
                                rules.get("rank_slight_delta", 0.8), rules.get("rank_strong_delta", 2.5), True)
            hits = []
            if not partial_month and lead and clicks is not None and previous and previous["clicks"] is not None and primary and previous_word:
                if previous["clicks"] - clicks >= min_loss and rank is not None and previous_word["position"] is not None and rank - previous_word["position"] >= 1.5 and (buyer or page["page_type"] in {"product", "category"}):
                    hits.append("recover")
            if page["page_type"] in {"product", "category"} and clicks is not None and clicks <= product_max_clicks and primary and rank is not None and 4 < rank <= 20 and (query_imp or 0) >= min_imp:
                if volume and volume["volume"] is not None and volume["volume"] >= min_vol and query_clicks is not None and query_clicks / max(query_imp, 1) <= low_ctr:
                    hits.append("product_win")
            if not partial_month and page["page_type"] == "blog" and buyer and current and previous and prior:
                sequence = [prior["clicks"], previous["clicks"], current["clicks"]]
                if all(x is not None for x in sequence) and sequence[0] - sequence[1] >= min_loss and sequence[1] - sequence[2] >= min_loss:
                    hits.append("buyer_decay")
            if primary and rank is not None and rank <= 20 and (query_imp or 0) >= min_imp and query_clicks is not None and query_clicks / max(query_imp, 1) <= low_ctr:
                hits.append("low_ctr")
            if primary and rank is not None and 10 < rank <= 20 and (query_imp or 0) >= min_imp:
                hits.append("page_two")
            if leads and lead == 0 and clicks is not None and clicks >= 3:
                hits.append("traffic_no_leads")
            if primary and len(word_pages[primary["keyword_id"]]) > 1:
                hits.append("multi_url")
            if page["page_type"] in {"product", "category"} and primary and (query_imp or 0) >= min_imp and query_clicks == 0:
                hits.append("zero_click")
            missing = []
            if current is None: missing.append("search_page")
            if primary is None: missing.append("search_page_keyword")
            if demand is None or (primary and volume is None): missing.append("keyword_demand")
            if leads is None: missing.append("inquiry")
            level = min((next(level for key, _, level in PRESETS if key == hit) for hit in hits),
                        key=lambda value: LEVEL_ORDER[value], default="normal")
            result.append({**page, "clicks": clicks, "impressions": impressions, "normal_inquiries": lead,
                           "primary_keyword": primary["query"] if primary else None,
                           "keyword_clicks": query_clicks, "keyword_impressions": query_imp,
                           "position": rank, "volume": volume["volume"] if volume else None,
                           "volume_snapshot": volume["snapshot_month"] if volume else None,
                           "matched_presets": hits, "attention_level": level, "missing_inputs": missing,
                           "traffic_tier": traffic_tier, "lead_tier": lead_tier,
                           "click_trend": click_trend, "rank_band": rank_band,
                           "rank_trend": rank_trend, "rule_version": rule_version,
                           "evidence": {"previous_clicks": previous["clicks"] if previous else None,
                                        "previous_position": previous_word["position"] if previous_word else None,
                                        "older_clicks": prior["clicks"] if prior else None}})
        if preset:
            result = [row for row in result if preset in row["matched_presets"]]
        if page_type:
            result = [row for row in result if row["page_type"] == page_type]
        if search:
            result = [row for row in result if search.casefold() in row["canonical_url"].casefold()]
        if facets:
            result = [row for row in result if all(row[key] in values for key, values in facets.items())]
        result.sort(key=lambda row: (LEVEL_ORDER[row["attention_level"]], -(row["normal_inquiries"] or 0),
                                     -(row["keyword_impressions"] or 0), row["page_id"]))
        total = len(result)
        return {"month": month, "partial_month": partial_month, "geo": geo, "demand_geo": demand_geo, "total": total,
                "rows": result[max(offset, 0):max(offset, 0) + min(max(limit, 1), 500)],
                "sources": {"search": perf, "query": queries, "demand": demand, "inquiry": leads}}


def explain_priority(site_id, page_id, month=None, geo=None, demand_geo=None):
    result = analyse(site_id, month=month, geo=geo, demand_geo=demand_geo, limit=500)
    row = next((item for item in result["rows"] if item["page_id"] == page_id), None)
    if row is None and result["total"] > 500:
        for offset in range(500, result["total"], 500):
            batch = analyse(site_id, month=month, geo=geo, demand_geo=demand_geo, limit=500, offset=offset)
            row = next((item for item in batch["rows"] if item["page_id"] == page_id), None)
            if row:
                break
    if row is None:
        raise ValueError("Unknown page_id")
    return {"page": row, "month": result["month"], "geo": result["geo"], "sources": result["sources"],
            "preset_definitions": [{"id": key, "name": name, "level": level} for key, name, level in PRESETS if key in row["matched_presets"]]}
