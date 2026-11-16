"""Single-command local launcher and import administration."""

import argparse
import json
import os
import threading
import webbrowser
from pathlib import Path

from .imports import import_file
from .storage import create_site, data_dir, list_sites


def main():
    parser = argparse.ArgumentParser(prog="seo-workbench")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local REST API and built frontend")
    serve.add_argument("--host", default=os.environ.get("SEO_PLATFORM_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("SEO_PLATFORM_PORT", "8766")))
    serve.add_argument("--no-browser", action="store_true")
    mcp = sub.add_parser("mcp", help="Start the AI tool server")
    mcp.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    mcp.add_argument("--port", type=int, default=8767)
    create = sub.add_parser("create-site")
    create.add_argument("name"); create.add_argument("domain"); create.add_argument("--timezone", default="UTC")
    imp = sub.add_parser("import")
    imp.add_argument("site_id"); imp.add_argument("dataset"); imp.add_argument("file")
    imp.add_argument("--source", required=True); imp.add_argument("--provider", default="file")
    imp.add_argument("--geo", default="GLOBAL"); imp.add_argument("--grain", default="month")
    imp.add_argument("--period"); imp.add_argument("--coverage", choices=["partial", "complete"], default="partial")
    imp.add_argument("--mapping", default="{}", help="JSON mapping of canonical field to source column")
    sub.add_parser("sites")
    sub.add_parser("data-dir")
    migrate = sub.add_parser("migrate-legacy")
    migrate.add_argument("old_db"); migrate.add_argument("--name", required=True)
    migrate.add_argument("--domain", required=True); migrate.add_argument("--timezone", default="UTC")
    adapter = sub.add_parser("sync-adapter", help="Run an installed provider adapter")
    adapter.add_argument("site_id"); adapter.add_argument("adapter")
    adapter.add_argument("start"); adapter.add_argument("end"); adapter.add_argument("--geo", default="GLOBAL")
    sub.add_parser("adapters", help="List installed provider adapters")
    args = parser.parse_args()
    if args.command == "serve":
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("The local server only binds to loopback. Configure authenticated deployment separately.")
        if not args.no_browser:
            threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
        import uvicorn
        uvicorn.run("seo_platform.api:app", host=args.host, port=args.port, log_level="info")
    elif args.command == "mcp":
        from .mcp_server import mcp as server
        if args.transport == "stdio":
            server.run()
        else:
            server.run(transport="streamable-http", host="127.0.0.1", port=args.port,
                       stateless_http=True, json_response=True)
    elif args.command == "create-site":
        print(json.dumps(create_site(args.name, args.domain, args.timezone), ensure_ascii=False, indent=2))
    elif args.command == "import":
        path = Path(args.file)
        result = import_file(args.site_id, path.read_bytes(), path.name, dataset=args.dataset,
                             source_name=args.source, provider=args.provider, geo=args.geo,
                             grain=args.grain, default_period=args.period,
                             coverage=args.coverage, mapping=json.loads(args.mapping), actor="cli")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "migrate-legacy":
        from .migration import migrate_legacy
        print(json.dumps(migrate_legacy(args.old_db, args.name, args.domain, args.timezone), ensure_ascii=False, indent=2))
    elif args.command == "sync-adapter":
        from .adapters import sync_adapter
        print(json.dumps(sync_adapter(args.site_id, args.adapter, args.start, args.end, args.geo), ensure_ascii=False, indent=2))
    elif args.command == "adapters":
        from .adapters import adapter_names
        print(json.dumps(adapter_names(), ensure_ascii=False, indent=2))
    elif args.command == "sites":
        print(json.dumps(list_sites(), ensure_ascii=False, indent=2))
    else:
        print(data_dir())


if __name__ == "__main__":
    main()
