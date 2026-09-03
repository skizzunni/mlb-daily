#!/bin/bash
# Hourly rebuild. Pure python + public MLB StatsAPI. No API keys, no LLM, no credits.
cd "$(dirname "$0")" || exit 1
/usr/bin/python3 build.py >> data/build.log 2>&1
/usr/bin/python3 artifact.py > site/board.html 2>> data/build.log
/usr/bin/python3 artifact.py --standalone > site/board_full.html 2>> data/build.log
exit 0
