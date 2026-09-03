"""
Weekly ESPN Fantasy Football Waiver Wire Bot
----------------------------------------------
Pulls your roster + free agents from ESPN, blends in Sleeper trending-add
data and Boris Chen 0.5 PPR tier rankings, and asks Claude to recommend
specific add/drops for your team.

League settings assumed: 0.5 PPR, 10 teams. Adjust POSITION_URLS if your
league uses standard or full PPR scoring instead.

Required environment variables (set as GitHub Actions secrets):
    ESPN_S2        - your espn_s2 cookie value
    ESPN_SWID      - your SWID cookie value (include the curly braces)
    ESPN_LEAGUE_ID - your league's numeric ID
    ESPN_TEAM_ID   - your team's numeric ID within the league
    ANTHROPIC_API_KEY - your Claude API key
    DISCORD_WEBHOOK_URL - (optional) webhook to post the report to

Install:
    pip install espn-api anthropic requests
"""

import os
import re
import requests
from espn_api.football import League
import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CURRENT_YEAR = 2026  # update each season, or compute from date if you prefer

# Boris Chen publishes plain-text tier files directly on S3 — no HTML
# parsing needed. "-HALF" suffix = 0.5 PPR variants; QB/K/DST have no PPR
# variant since reception scoring doesn't apply to them.
POSITION_URLS = {
    "WR": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_WR-HALF.txt",
    "RB": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_RB-HALF.txt",
    "TE": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_TE-HALF.txt",
    "FLEX": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_FLX-HALF.txt",
    "QB": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_QB.txt",
    "K": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_K.txt",
    "DST": "https://s3-us-west-1.amazonaws.com/fftiers/out/text_DST.txt",
}

SLEEPER_TRENDING_ADD_URL = (
    "https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=50"
)
SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------

def get_espn_league():
    return League(
        league_id=int(os.environ["ESPN_LEAGUE_ID"]),
        year=CURRENT_YEAR,
        espn_s2=os.environ["ESPN_S2"],
        swid=os.environ["ESPN_SWID"],
    )


def get_my_roster(league, team_id):
    team = next(t for t in league.teams if t.team_id == team_id)
    return [
        {
            "name": p.name,
            "position": p.position,
            "pro_team": p.proTeam,
            "injury_status": getattr(p, "injuryStatus", "ACTIVE"),
            "projected_points": round(getattr(p, "projected_total_points", 0), 1),
        }
        for p in team.roster
    ]


def get_free_agents(league, size=60):
    fas = league.free_agents(size=size)
    return [
        {
            "name": p.name,
            "position": p.position,
            "pro_team": p.proTeam,
            "percent_owned": getattr(p, "percent_owned", None),
            "projected_points": round(getattr(p, "projected_total_points", 0), 1),
        }
        for p in fas
    ]


# ---------------------------------------------------------------------------
# Sleeper (free, no auth) — leaguewide trending adds as an extra signal
# ---------------------------------------------------------------------------

def get_sleeper_trending_adds():
    trending = requests.get(SLEEPER_TRENDING_ADD_URL, timeout=15).json()
    all_players = requests.get(SLEEPER_PLAYERS_URL, timeout=30).json()

    results = []
    for entry in trending:
        pid = entry["player_id"]
        meta = all_players.get(pid)
        if not meta:
            continue
        name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
        results.append({"name": name, "adds_24h": entry["count"]})
    return results


# ---------------------------------------------------------------------------
# Boris Chen tiers — plain text files, no HTML parsing required
# ---------------------------------------------------------------------------

def get_tiers(url):
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    tiers = {}
    for line in resp.text.strip().splitlines():
        m = re.match(r"Tier (\d+):\s*(.+)", line.strip())
        if m:
            tier_num = int(m.group(1))
            players = [p.strip() for p in m.group(2).split(",")]
            tiers[tier_num] = players
    return tiers


def get_all_tiers():
    return {pos: get_tiers(url) for pos, url in POSITION_URLS.items()}


def player_tier_lookup(all_tiers):
    """Flatten to {player_name: (position, tier_num)} for quick lookups."""
    lookup = {}
    for pos, tiers in all_tiers.items():
        for tier_num, players in tiers.items():
            for name in players:
                lookup[name] = (pos, tier_num)
    return lookup


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def build_prompt(roster, free_agents, trending, tier_lookup):
    def annotate(players):
        out = []
        for p in players:
            tier_info = tier_lookup.get(p["name"])
            tier_str = f"Tier {tier_info[1]}" if tier_info else "Unranked/no tier data"
            out.append(f"- {p['name']} ({p['position']}, {p['pro_team']}) "
                        f"proj:{p['projected_points']} tier:{tier_str}")
        return "\n".join(out)

    trending_str = "\n".join(
        f"- {t['name']}: {t['adds_24h']} adds in last 24h" for t in trending[:25]
    )

    return f"""You are helping manage a 0.5 PPR, 10-team ESPN fantasy football team.

MY CURRENT ROSTER:
{annotate(roster)}

AVAILABLE FREE AGENTS (top {len(free_agents)} by ESPN ownership):
{annotate(free_agents)}

LEAGUEWIDE TRENDING ADDS (Sleeper, last 24h, cross-platform signal):
{trending_str}

Recommend up to 3 specific add/drop moves for this week. For each:
1. Name the drop (weakest roster piece — factor in tier, projection, bye weeks, injury status)
2. Name the add (best available free agent for that roster spot)
3. Give a 1-2 sentence rationale referencing the tier data and/or trending data
4. Flag priority (high/medium/low waiver priority or FAAB-worthy)

If no moves are clearly worth making, say so plainly rather than forcing recommendations."""


def get_recommendations(prompt):
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # cheap enough to run weekly; bump to
                                             # claude-sonnet-5 if you want deeper reasoning
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def post_to_discord(message):
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        return
    # Discord caps messages at 2000 chars; split if needed
    for i in range(0, len(message), 1900):
        requests.post(webhook, json={"content": message[i:i + 1900]}, timeout=15)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    league = get_espn_league()
    team_id = int(os.environ["ESPN_TEAM_ID"])

    roster = get_my_roster(league, team_id)
    free_agents = get_free_agents(league)
    trending = get_sleeper_trending_adds()
    all_tiers = get_all_tiers()
    tier_lookup = player_tier_lookup(all_tiers)

    prompt = build_prompt(roster, free_agents, trending, tier_lookup)
    recommendations = get_recommendations(prompt)

    report = f"🏈 Weekly Waiver Wire Report\n\n{recommendations}"
    print(report)
    post_to_discord(report)


if __name__ == "__main__":
    main()
