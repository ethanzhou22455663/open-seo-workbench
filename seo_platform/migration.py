"""Read-only migration of the original single-site SQLite layout."""

import hashlib
import json
import sqlite3
from pathlib import Path

from .storage import (audit, create_site, ensure_source, list_sites, set_config,
                      site_db, site_info, utc_now)


def migrate_legacy(old_path, name, domain, timezone="UTC"):
    old_path = Path(old_path).expanduser().resolve()
    if not old_path.is_file():
        raise ValueError("Legacy database does not exist")
    old = sqlite3.connect(f"file:{old_path.as_posix()}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    try:
        required = {"pages", "keywords", "gsc_page_monthly", "gsc_page_keyword_monthly"}
        present = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= present:
            raise ValueError("Not a supported legacy SEO database")
        existing = next((s for s in list_sites() if s["primary_domain"] == domain.lower().removeprefix("www.")), None)
        site = site_info(existing["site_id"]) if existing else create_site(name, domain, timezone)
        site_id = site["site_id"]
        counts = {}
        with site_db(site_id) as db:
            if db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]:
                raise ValueError("Target site already contains pages; migration must start with an empty site")
            with db:
                for r in old.execute("SELECT page_id,url,page_type,language,business_priority,first_seen_at FROM pages"):
                    db.execute("INSERT INTO pages(page_id,canonical_url,page_type,page_type_origin,language,business_priority,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
                               (r["page_id"], r["url"], r["page_type"], "legacy", r["language"], r["business_priority"], r["first_seen_at"], utc_now()))
                counts["pages"] = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
                if "page_url_aliases" in present:
                    for r in old.execute("SELECT page_id,url,first_seen_at FROM page_url_aliases"):
                        db.execute("INSERT INTO page_aliases VALUES(?,?,?)", (r["url"], r["page_id"], r["first_seen_at"]))
                for r in old.execute("SELECT keyword_id,query,normalized_query FROM keywords"):
                    db.execute("INSERT INTO keywords(keyword_id,query,normalized_query) VALUES(?,?,?)",
                               (r["keyword_id"], r["query"], r["normalized_query"]))
                counts["keywords"] = db.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
                gsc = ensure_source(db, "Legacy GSC", "gsc", "legacy_sqlite")
                ahrefs = ensure_source(db, "Legacy Ahrefs", "ahrefs", "legacy_sqlite")
                inquiries = ensure_source(db, "Legacy inquiries", "excel", "legacy_sqlite")
                def run(source_id, dataset, count):
                    digest = hashlib.sha256(f"legacy|{old_path}|{dataset}|{old_path.stat().st_size}".encode()).hexdigest()
                    return db.execute("INSERT INTO import_runs(source_id,dataset,grain,geo,fingerprint,status,row_count,coverage,started_at,finished_at) VALUES(?,?,?,?,?,'complete',?,'complete',?,?)",
                                      (source_id, dataset, "month", "GLOBAL", digest, count, utc_now(), utc_now())).lastrowid
                for suffix, grain in (("monthly", "month"), ("daily", "day")):
                    for old_table, new_table, dataset in ((f"gsc_page_{suffix}", "search_page_metrics", "search_page"),
                                                          (f"gsc_page_keyword_{suffix}", "search_page_keyword_metrics", "search_page_keyword")):
                        if old_table not in present:
                            continue
                        n = old.execute(f"SELECT COUNT(*) FROM {old_table}").fetchone()[0]
                        rid = run(gsc, f"{dataset}_{grain}", n)
                        for row in old.execute(f"SELECT * FROM {old_table}"):
                            geo = "GLOBAL" if row["country"] == "ALL" else row["country"].upper()
                            if dataset == "search_page":
                                db.execute("INSERT INTO search_page_metrics VALUES(?,?,?,?,?,?,?,?,?)",
                                           (row["page_id"], row["month" if grain == "month" else "day"], grain,
                                            geo, gsc, row["clicks"], row["impressions"], row["position"], rid))
                            else:
                                db.execute("INSERT INTO search_page_keyword_metrics VALUES(?,?,?,?,?,?,?,?,?,?)",
                                           (row["page_id"], row["keyword_id"], row["month" if grain == "month" else "day"], grain,
                                            geo, gsc, row["clicks"], row["impressions"], row["position"], rid))
                        counts[old_table] = n
                if "ahrefs_keyword_monthly" in present:
                    n = old.execute("SELECT COUNT(*) FROM ahrefs_keyword_monthly").fetchone()[0]
                    rid = run(ahrefs, "keyword_demand", n)
                    for r in old.execute("SELECT * FROM ahrefs_keyword_monthly"):
                        db.execute("INSERT INTO keyword_demand VALUES(?,?,?,?,?,?,?,?)",
                                   (r["keyword_id"], r["snapshot_month"], r["market"].upper(), ahrefs,
                                    r["volume"], None, None, rid))
                    counts["keyword_demand"] = n
                if "inquiries" in present:
                    n = old.execute("SELECT COUNT(*) FROM inquiries").fetchone()[0]
                    rid = run(inquiries, "inquiry", n)
                    for r in old.execute("SELECT * FROM inquiries"):
                        db.execute("""INSERT INTO inquiries(source_id,external_id,submitted_on,landing_page_id,submit_page_id,
                           landing_url,submit_url,quality,attribution,geo,buyer_country,utm_source,utm_medium,
                           source_label,source_record_id,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (inquiries, r["dedup_key"], r["submitted_on"], r["landing_page_id"], r["submit_page_id"],
                            r["landing_url"], r["submit_url"], r["quality"], r["source"], "GLOBAL", r["country"],
                            r["utm_source"], r["utm_medium"], r["source_file"], r["source_record_id"], rid))
                    counts["inquiries"] = n
                if "seo_tasks" in present:
                    for r in old.execute("SELECT * FROM seo_tasks"):
                        db.execute("INSERT INTO seo_tasks(task_id,keyword_id,page_id,proposed_page_type,status,note,created_at) VALUES(?,?,?,?,?,?,?)",
                                   (r["task_id"], r["keyword_id"], r["page_id"], r["proposed_page_type"], r["status"], r["note"], r["created_at"]))
                set_config(db, "default_geo", "GLOBAL")
                preferred_geo = "US" if db.execute("SELECT 1 FROM keyword_demand WHERE geo='US' LIMIT 1").fetchone() else "GLOBAL"
                set_config(db, "default_demand_geo", preferred_geo)
                audit(db, "migration", "migrate_legacy", {"source_path_hash": hashlib.sha256(str(old_path).encode()).hexdigest(),
                                                       "counts": counts})
            for source_table, target_table in (("pages", "pages"), ("keywords", "keywords"),
                                                ("gsc_page_monthly", "search_page_metrics"),
                                                ("gsc_page_keyword_monthly", "search_page_keyword_metrics")):
                old_count = old.execute(f"SELECT COUNT(*) FROM {source_table}").fetchone()[0]
                new_count = db.execute(f"SELECT COUNT(*) FROM {target_table}" +
                                       (" WHERE grain='month'" if target_table.startswith("search_") else "")).fetchone()[0]
                if old_count != new_count:
                    raise RuntimeError(f"Migration count mismatch: {source_table} {old_count} != {new_count}")
        return {"site_id": site_id, "domain": site["primary_domain"], "counts": counts,
                "source_database_unchanged": True}
    finally:
        old.close()
