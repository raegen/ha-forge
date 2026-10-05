#!/usr/bin/with-contenv bashio
set -e
bashio::log.info "Starting Forge"
exec python3 /forge.py
