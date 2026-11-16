#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ -z "${SEO_PLATFORM_DATA_DIR:-}" ] && [ -f data/registry.sqlite3 ]; then
  export SEO_PLATFORM_DATA_DIR="$(pwd)/data"
fi
if [ ! -x .venv/bin/seo-workbench ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -e .
fi
cd frontend
if [ ! -d node_modules ]; then npm ci; fi
npm run build
cd ..
exec .venv/bin/seo-workbench serve "$@"
