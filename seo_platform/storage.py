"""Registry and isolated SQLite databases. No client data lives in source control."""

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir():
    override = os.environ.get("SEO_PLATFORM_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "open-seo-workbench"


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
 site_id TEXT PRIMARY KEY, name TEXT NOT NULL, primary_domain TEXT NOT NULL UNIQUE,
 timezone TEXT NOT NULL DEFAULT 'UTC', db_file TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

SITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_config (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_versions (
 version INTEGER PRIMARY KEY, rules_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
 page_id INTEGER PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE, page_type TEXT NOT NULL DEFAULT 'other',
 page_type_origin TEXT NOT NULL DEFAULT 'auto',
 language TEXT, business_priority INTEGER NOT NULL DEFAULT 3 CHECK(business_priority BETWEEN 1 AND 5),
 first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS page_aliases (
 alias_url TEXT PRIMARY KEY, page_id INTEGER NOT NULL REFERENCES pages(page_id), first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keywords (
 keyword_id INTEGER PRIMARY KEY, query TEXT NOT NULL, normalized_query TEXT NOT NULL UNIQUE, language TEXT
);
CREATE TABLE IF NOT EXISTS data_sources (
 source_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
 kind TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_definitions (
 metric_key TEXT PRIMARY KEY, label TEXT NOT NULL, entity_scope TEXT NOT NULL,
 unit TEXT NOT NULL, aggregation TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', builtin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS source_preferences (
 metric_key TEXT NOT NULL REFERENCES metric_definitions(metric_key), geo TEXT NOT NULL,
 priority_json TEXT NOT NULL, PRIMARY KEY(metric_key,geo)
);
CREATE TABLE IF NOT EXISTS import_runs (
 run_id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES data_sources(source_id),
 dataset TEXT NOT NULL, grain TEXT, geo TEXT, period_start TEXT, period_end TEXT,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0,
 coverage TEXT NOT NULL DEFAULT 'complete', started_at TEXT NOT NULL, finished_at TEXT, error TEXT,
 UNIQUE(source_id,dataset,fingerprint)
);
CREATE TABLE IF NOT EXISTS search_page_metrics (
 page_id INTEGER NOT NULL REFERENCES pages(page_id), period TEXT NOT NULL, grain TEXT NOT NULL,
 geo TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES data_sources(source_id),
 clicks INTEGER, impressions INTEGER, position REAL, run_id INTEGER REFERENCES import_runs(run_id),
 PRIMARY KEY(page_id,period,grain,geo,source_id)
);
CREATE TABLE IF NOT EXISTS search_page_keyword_metrics (
 page_id INTEGER NOT NULL REFERENCES pages(page_id), keyword_id INTEGER NOT NULL REFERENCES keywords(keyword_id),
 period TEXT NOT NULL, grain TEXT NOT NULL, geo TEXT NOT NULL,
 source_id INTEGER NOT NULL REFERENCES data_sources(source_id),
 clicks INTEGER, impressions INTEGER, position REAL, run_id INTEGER REFERENCES import_runs(run_id),
 PRIMARY KEY(page_id,keyword_id,period,grain,geo,source_id)
);
CREATE TABLE IF NOT EXISTS keyword_demand (
 keyword_id INTEGER NOT NULL REFERENCES keywords(keyword_id), snapshot_month TEXT NOT NULL,
 geo TEXT NOT NULL, source_id INTEGER NOT NULL REFERENCES data_sources(source_id),
 volume INTEGER, difficulty REAL, cpc REAL, run_id INTEGER REFERENCES import_runs(run_id),
 PRIMARY KEY(keyword_id,snapshot_month,geo,source_id)
);
CREATE TABLE IF NOT EXISTS inquiries (
 inquiry_id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES data_sources(source_id),
 external_id TEXT NOT NULL, submitted_on TEXT, landing_page_id INTEGER REFERENCES pages(page_id),
 submit_page_id INTEGER REFERENCES pages(page_id), landing_url TEXT, submit_url TEXT,
 quality TEXT, attribution TEXT, geo TEXT, buyer_country TEXT, utm_source TEXT,
 utm_medium TEXT, source_label TEXT, source_record_id TEXT, run_id INTEGER REFERENCES import_runs(run_id),
 UNIQUE(source_id,external_id)
);
CREATE TABLE IF NOT EXISTS custom_metrics (
 entity_scope TEXT NOT NULL, entity_id INTEGER NOT NULL, metric_key TEXT NOT NULL REFERENCES metric_definitions(metric_key),
 period TEXT NOT NULL, grain TEXT NOT NULL, geo TEXT NOT NULL,
 source_id INTEGER NOT NULL REFERENCES data_sources(source_id), value_number REAL,
 value_text TEXT, run_id INTEGER REFERENCES import_runs(run_id),
 PRIMARY KEY(entity_scope,entity_id,metric_key,period,grain,geo,source_id)
);
CREATE TABLE IF NOT EXISTS seo_tasks (
 task_id INTEGER PRIMARY KEY, keyword_id INTEGER REFERENCES keywords(keyword_id), page_id INTEGER REFERENCES pages(page_id),
 proposed_page_type TEXT, status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
 audit_id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_page_metrics_period ON search_page_metrics(period,grain,geo,source_id,page_id);
CREATE INDEX IF NOT EXISTS idx_query_metrics_period ON search_page_keyword_metrics(period,grain,geo,source_id,page_id);
CREATE INDEX IF NOT EXISTS idx_demand_geo ON keyword_demand(geo,snapshot_month,keyword_id);
CREATE INDEX IF NOT EXISTS idx_inquiry_landing ON inquiries(landing_page_id,submitted_on,quality);
CREATE INDEX IF NOT EXISTS idx_custom_metric ON custom_metrics(metric_key,period,grain,geo,entity_scope);
"""

BUILTIN_METRICS = (
    ("search_clicks", "搜索点击", "page", "count", "sum", "来自搜索结果的点击"),
    ("search_impressions", "搜索展示", "page", "count", "sum", "搜索结果展示次数"),
    ("search_position", "平均排名", "page_keyword", "position", "weighted_mean", "页面与查询词的平均位置"),
    ("keyword_volume", "月搜索量", "keyword", "monthly_estimate", "latest", "供应商估算月搜索量"),
    ("normal_inquiries", "有效落地询盘", "page", "count", "sum", "关联落地页的有效询盘；来源需单独核实"),
)

DEFAULT_SITE_SETTINGS = {
    "default_geo": "GLOBAL", "default_demand_geo": "GLOBAL", "language_filter": None,
    "page_type_patterns": [],
    "intent_terms": ["manufacturer", "supplier", "factory", "wholesale", "oem", "distributor"],
    "product_query_exclude_terms": ["how", "why", "what", "method", "guide", "install", "repair", "clean", "difference", "versus"],
    "rules": {"primary_min_impressions": 15, "min_impressions": 50,
              "min_volume": 50, "product_max_clicks": 5,
              "low_ctr": 0.01, "min_click_loss": 2, "lead_lookback_days": 365,
              "traffic_high_product": 3, "traffic_high_category": 3,
              "traffic_high_blog": 6, "traffic_high_other": 3,
              "lead_high_count": 2, "click_slight_delta": 2,
              "click_strong_delta": 6, "rank_slight_delta": 0.8,
              "rank_strong_delta": 2.5},
    "rules_version": 1,
}


def registry():
    db = _connect(data_dir() / "registry.sqlite3")
    db.executescript(REGISTRY_SCHEMA)
    return db


def list_sites():
    db = registry()
    try:
        return [dict(row) for row in db.execute("SELECT site_id,name,primary_domain,timezone,created_at FROM sites ORDER BY name")]
    finally:
        db.close()


def site_info(site_id):
    db = registry()
    try:
        row = db.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        raise ValueError(f"Unknown site_id: {site_id}")
    return dict(row)


def normalize_domain(value):
    raw = value if "://" in value else "https://" + value
    parsed = urlsplit(raw)
    domain = (parsed.hostname or "").lower().rstrip(".")
    if not domain or not re.fullmatch(r"[a-z0-9.-]+", domain) or ".." in domain:
        raise ValueError("A valid domain is required")
    return domain


def create_site(name, domain, timezone_name="UTC", config=None):
    from zoneinfo import ZoneInfo
    ZoneInfo(timezone_name)
    domain = normalize_domain(domain)
    site_id = str(uuid.uuid4())
    filename = f"sites/{site_id}.sqlite3"
    db = registry()
    try:
        db.execute("INSERT INTO sites VALUES(?,?,?,?,?,?)", (site_id, name.strip() or domain, domain, timezone_name, filename, utc_now()))
        db.commit()
    finally:
        db.close()
    with site_db(site_id) as db:
        with db:
            for key, value in (config or {}).items():
                set_config(db, key, value)
    return site_info(site_id)


@contextmanager
def site_db(site_id):
    info = site_info(site_id)
    file = (data_dir() / info["db_file"]).resolve()
    if not file.is_relative_to(data_dir().resolve()):
        raise ValueError("Invalid database path")
    db = _connect(file)
    db.executescript(SITE_SCHEMA)
    db.executemany("INSERT OR IGNORE INTO metric_definitions VALUES(?,?,?,?,?,?,1)", BUILTIN_METRICS)
    db.executemany("INSERT OR IGNORE INTO site_config VALUES(?,?)",
                   [(key, json.dumps(value, ensure_ascii=False)) for key, value in DEFAULT_SITE_SETTINGS.items()])
    rules = config_value(db, "rules", DEFAULT_SITE_SETTINGS["rules"])
    db.execute("INSERT OR IGNORE INTO rule_versions VALUES(1,?,?)", (json.dumps(rules, ensure_ascii=False), utc_now()))
    if set(DEFAULT_SITE_SETTINGS["rules"]) - set(rules):
        rules = {**DEFAULT_SITE_SETTINGS["rules"], **rules}
        version = config_value(db, "rules_version", 1) + 1
        set_config(db, "rules", rules)
        set_config(db, "rules_version", version)
        db.execute("INSERT INTO rule_versions VALUES(?,?,?)", (version, json.dumps(rules, ensure_ascii=False), utc_now()))
    db.commit()
    try:
        yield db
    finally:
        db.close()


def config_value(db, key, default=None):
    row = db.execute("SELECT value_json FROM site_config WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_config(db, key, value):
    db.execute("INSERT INTO site_config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (key, json.dumps(value, ensure_ascii=False)))


def normalize_url(raw, domain):
    if not raw:
        return None
    parsed = urlsplit(str(raw).strip())
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
        return None
    host = parsed.hostname.lower().removeprefix("www.")
    owner = domain.lower().removeprefix("www.")
    if host != owner and not host.endswith("." + owner):
        return None
    path = re.sub(r"/+", "/", parsed.path or "/")
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/") and not re.search(r"\.[a-z0-9]{1,6}$", path, re.I):
        path += "/"
    return urlunsplit(("https", host, path, "", ""))


def classify_page(url, patterns):
    path = urlsplit(url).path
    if path == "/":
        return "home"
    if re.search(r"\.(?:pdf|jpe?g|png|webp|gif|svg|avif|ico|bmp)$", path, re.I):
        return "asset"
    for rule in patterns:
        if re.search(rule["pattern"], path, re.I):
            return rule["type"]
    return "other"


def ensure_page(db, raw_url, domain, page_type=None, language=None):
    url = normalize_url(raw_url, domain)
    if not url:
        return None
    row = db.execute("SELECT page_id FROM pages WHERE canonical_url=?", (url,)).fetchone()
    if not row:
        row = db.execute("SELECT page_id FROM page_aliases WHERE alias_url=?", (url,)).fetchone()
    if row:
        return row[0]
    inferred = page_type or classify_page(url, config_value(db, "page_type_patterns", []))
    return db.execute("INSERT INTO pages(canonical_url,page_type,language,first_seen_at,last_seen_at) VALUES(?,?,?,?,?)",
                      (url, inferred, language, utc_now(), utc_now())).lastrowid


def ensure_keyword(db, query, language=None):
    query = str(query or "").strip()
    if not query:
        return None
    norm = " ".join(query.casefold().split())
    row = db.execute("SELECT keyword_id FROM keywords WHERE normalized_query=?", (norm,)).fetchone()
    if row:
        return row[0]
    return db.execute("INSERT INTO keywords(query,normalized_query,language) VALUES(?,?,?)", (query, norm, language)).lastrowid


def ensure_source(db, name, provider="file", kind="import", config=None):
    row = db.execute("SELECT source_id FROM data_sources WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return db.execute("INSERT INTO data_sources(name,provider,kind,config_json,created_at) VALUES(?,?,?,?,?)",
                      (name, provider, kind, json.dumps(config or {}), utc_now())).lastrowid


def audit(db, actor, action, detail):
    db.execute("INSERT INTO audit_log(actor,action,detail_json,created_at) VALUES(?,?,?,?)",
               (actor, action, json.dumps(detail, ensure_ascii=False), utc_now()))
