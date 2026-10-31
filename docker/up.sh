#!/bin/sh
# Build wheels of the local libraries and start the test instance on http://localhost:8123.
set -eu
cd "$(dirname "$0")"
rm -rf wheels
mkdir wheels
uv build --wheel --out-dir wheels ../../gtfs-hub
uv build --wheel --out-dir wheels ../../gtfs-hub-gzm
docker compose up --build --detach
