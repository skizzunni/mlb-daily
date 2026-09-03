# mlb-daily

Self-updating MLB model + picks site. **No API keys, no LLM, no usage credits.**
Pure Python stdlib against the public MLB StatsAPI.

## Start it

```bash
launchctl load ~/Library/LaunchAgents/com.skizzy.mlbdaily.build.plist
launchctl load ~/Library/LaunchAgents/com.skizzy.mlbdaily.serve.plist
```

- Rebuilds every hour (`StartInterval 3600`), also on login.
- Serves `site/` at <http://localhost:8765> — reachable from your phone on the
  same wifi at `http://<your-mac-ip>:8765` (`ipconfig getifaddr en0`).

Stop it:

```bash
launchctl unload ~/Library/LaunchAgents/com.skizzy.mlbdaily.build.plist
launchctl unload ~/Library/LaunchAgents/com.skizzy.mlbdaily.serve.plist
```

Only runs while the Mac is awake. For a real URL that updates with the Mac off,
push this repo to GitHub and enable Pages — `.github/workflows/update.yml` runs
the same build hourly on GitHub's runners, free for public repos.

## Files

| file | what it does |
|---|---|
| `model.py` | data fetching + the run-expectancy model + simulator |
| `build.py` | runs the model over today's slate, writes `site/index.html` |
| `backtest.py` | grades the model against completed games |
| `data/history.json` | every pick, locked at first pitch, auto-graded |
| `data/lines.json` | *optional* real book lines, keyed by gamePk (see below) |

## The model

1. **Starters** → regressed RA/9. FIP components (HR, BB, K) for the current
   season are blended with the two prior seasons — each re-centered onto this
   year's run environment — *then* regressed toward league average by total
   batters faced. This is why a shiny ERA on a bad process gets discounted
   (Cal Quantrill: 3.01 ERA, 4.07 FIP, 5.25 prior → 4.75 RA/9).
2. **Bullpens** → same treatment on relief-only splits, covering the innings
   the starter is not projected to reach.
3. **Offense** → season R/G with the team's *own* home park divided out, nudged
   by 3-week form.
4. **Matchup** → `exp_runs = neutral_RS × opp_staff_RA9 / league_R/G`, then park,
   then home-field (×1.034, which reproduces MLB's real ~53% home win rate).
5. **Simulation** → 30,000 negative-binomial draws per side. Dispersion is
   calibrated to 8.0, which reproduces Pythagenpat to within 0.2%. (The obvious
   value from season-long variance, ~4.2, is wrong: conditional on a known
   matchup the residual variance is much lower.)

## Backtest — Aug 1 to Sep 2, 2026, 447 games

| tier | record | hit rate |
|---|---|---|
| PLAY (≥60%) | 60-39 | 60.6% |
| LEAN (55.5–60%) | 88-55 | 61.5% |
| PASS (<55.5%) | 98-107 | **47.8%** |

PLAY + LEAN: **148-94 (61.2%)**. The PASS tier finishing under 50% is the point
of the tiering — those games are not bettable.

**Caveat:** the backtest applies current-season stats to past games, so it
flatters the model. It is a calibration check, not a profit claim. No closing
prices were captured, so it says nothing about beating the vig.

### What 61.2% means when parlayed

| legs | hit rate |
|---|---|
| 1 | 61.2% |
| 2 | 37% |
| 3 | 23% |
| 5 | 9% |
| 9 | **1.2%** |

A nine-leg card needs every leg. Going 6-3 pays exactly what 0-9 pays.

## Real sportsbook lines (optional)

The model prints *fair* odds, never a book's. It will never invent a line.
To compare against real prices, drop them in `data/lines.json`:

```json
{ "823907": { "ml_away": "+145", "ml_home": "-172", "total": "8.5",
              "source": "DraftKings 11:20am" } }
```

Keys are `gamePk` (in `site/data.json`). Anything present renders on the card;
anything absent stays blank.
