# Fantasy Waiver Wire Bot

Weekly automated waiver-wire recommendations for a 0.5 PPR, 10-team ESPN
fantasy football league. Pulls your roster and free agents from ESPN,
blends in Sleeper trending-add data and Boris Chen tier rankings, and asks
Claude to recommend specific add/drops — posted to Discord every Tuesday.

## How it works

1. `espn-api` pulls your roster + top free agents
2. Sleeper's public API pulls leaguewide trending adds (last 24h)
3. Boris Chen's plain-text tier files (S3-hosted, FantasyPros-sourced) add
   tier context for every player
4. Everything gets blended into one prompt sent to Claude
5. Claude's recommendations get posted to a Discord webhook
6. A GitHub Actions cron job runs the whole thing every Tuesday morning

## Setup

1. Get your ESPN cookies: log into ESPN Fantasy, open browser dev tools →
   Application/Storage → Cookies, grab `espn_s2` and `SWID` (keep the
   curly braces on SWID).
2. Find your league ID and team ID from your league's ESPN URL
   (`leagueId=XXXXX`, `teamId=X`).
3. Get a Claude API key from [console.anthropic.com](https://console.anthropic.com).
4. (Optional) Create a Discord webhook: Server Settings → Integrations →
   Webhooks → New Webhook.
5. Add all five as repo secrets under **Settings → Secrets and variables →
   Actions**:
   - `ESPN_S2`
   - `ESPN_SWID`
   - `ESPN_LEAGUE_ID`
   - `ESPN_TEAM_ID`
   - `ANTHROPIC_API_KEY`
   - `DISCORD_WEBHOOK_URL` (optional)
6. Put `waiver_wire_bot.py` in the repo root and `waiver-report.yml` in
   `.github/workflows/`.
7. Trigger it manually from the Actions tab (`workflow_dispatch`) to test,
   or wait for the Tuesday schedule.

## Local run

```bash
pip install espn-api anthropic requests
export ESPN_S2=... ESPN_SWID=... ESPN_LEAGUE_ID=... ESPN_TEAM_ID=... ANTHROPIC_API_KEY=...
python waiver_wire_bot.py
```

## Notes

- Update `CURRENT_YEAR` in `waiver_wire_bot.py` each season.
- Boris Chen tier URLs use the `-HALF` suffix for 0.5 PPR scoring at WR/RB/TE/FLEX;
  swap to the standard or full-PPR variants if your league scoring changes.
- ESPN's endpoints are unofficial and can change without notice — if a run
  fails, check `espn-api`'s GitHub issues first.
