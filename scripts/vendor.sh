#!/bin/bash
# Pin the two front-end libraries into static/vendor, so the page has no CDN
# dependency at runtime. Re-run only to bump a version, then commit the files.
set -euo pipefail
D="$(cd "$(dirname "$0")/.." && pwd)/worklog/static/vendor"
mkdir -p "$D"
curl -fsSL https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js -o "$D/echarts.min.js"
curl -fsSL https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/dist/js/tabulator.min.js -o "$D/tabulator.min.js"
curl -fsSL https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/dist/css/tabulator.min.css -o "$D/tabulator.min.css"
ls -l "$D"
