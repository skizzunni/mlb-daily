#!/bin/bash
# Hourly rebuild. Pure python + public MLB StatsAPI. No API keys, no LLM, no credits.
cd "$(dirname "$0")" || exit 1
exec /usr/bin/python3 build.py >> data/build.log 2>&1
