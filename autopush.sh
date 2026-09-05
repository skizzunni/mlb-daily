#!/bin/bash
# Rebuild both boards and PUSH. This is the fix for "the sites don't update".
#
# GitHub throttles scheduled workflows on public repos hard -- measured gaps of
# 105 to 292 minutes against a 10-minute cron, with zero failed runs. No cron
# expression changes that. But a PUSH triggers the workflow immediately, every
# time, and pushing needs no API token because the SSH key is already trusted.
#
# So this rebuilds locally, commits only when something actually changed, and
# pushes. The push wakes Actions, Actions rebuilds on its own runner and
# deploys. Runs whenever the Mac is awake.
#
# Installed by com.skizzy.autopush.plist. Log: data/autopush.log
set -u
PY=/usr/bin/python3
LOG=/Users/skizzy/mlb-daily/data/autopush.log
stamp() { date "+%Y-%m-%d %H:%M:%S"; }
say() { echo "[$(stamp)] $*" >> "$LOG"; }

push_repo() {
  local dir="$1" branch="$2" build="$3"
  cd "$dir" || { say "$dir: missing"; return 1; }

  # Always rebase first: the Actions bot commits graded results back, so the
  # remote is routinely ahead. Skipping this was what made pushes fail.
  git pull --rebase --autostash -q origin "$branch" 2>/dev/null || {
    say "$dir: pull failed, skipping this cycle"; return 1; }

  eval "$build" >> "$LOG" 2>&1
  local rc=$?
  if [ $rc -ne 0 ]; then
    say "$dir: build exited $rc -- not pushing a broken board"
    return 1
  fi

  if git diff --quiet && git diff --cached --quiet; then
    say "$dir: no change"
    return 0
  fi
  git add -A
  git -c user.name="skizzy" -c user.email="sky.zoo.2004@gmail.com" \
      commit -q -m "auto: rebuild $(stamp)" 2>/dev/null
  if git push -q origin "HEAD:$branch" 2>>"$LOG"; then
    say "$dir: pushed"
  else
    say "$dir: push rejected"
  fi
}

push_repo /Users/skizzy/mlb-daily main \
  "$PY build.py && $PY artifact.py > site/board.html"

push_repo /Users/skizzy/HEHE claude/mlb-picks-review-jlx9w1 \
  "$PY rebuild_board.py"

exit 0
