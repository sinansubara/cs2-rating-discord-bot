# CS2 HLTV Rating Discord Bot

A Discord bot that pulls your **FACEIT CS2** match stats and calculates
approximate **HLTV Rating 2.0, 2.1, and ≈3.0** for any player.

---

## Commands

| Command | Description |
|---------|-------------|
| `/rating <username>` | Rating for the player's **last match** |
| `/rating <username> maps:5` | Average rating across last 5 maps |
| `/card <username> maps:10` | Shareable player card summary |
| `/matchrating <username> <match_id>` | Rating for a specific match ID |
| `/analyze <username> maps:10` | Last N maps analysis: trend, consistency, role, best/worst map |
| `/role <username> maps:20` | Role classifier with confidence |
| `/compare <player_a> <player_b> maps:10` | Side-by-side comparison over recent maps |
| `/teamcompare <p1> <p2> <p3> [p4] [p5]` | Team averages + balance snapshot |
| `/rivalry <player_a> <player_b> maps:10` | Head-to-head over shared recent maps |
| `/maps <username> maps:15` | Per-map rating breakdown (2.1 + KD) |
| `/highlights <username> maps:10` | Best map + clutches + multi-kill highlights |
| `/session <username> recent_maps:5 baseline_maps:20` | Detect hot/cold form vs baseline |
| `/weeklyreport <username> maps:10` | Generate weekly report card instantly |
| `/weeklygraph <username> maps:10` | Weekly trend sparklines for rating + ADR |
| `/weeklysubscribe <username> [channel]` | Auto-post weekly report every Monday 09:00 UTC |
| `/weeklyunsubscribe` | Disable weekly auto report |
| `/alert <username> rating:1.20` | DM when rating crosses threshold |
| `/alertlist` | List your active alerts |
| `/alertremove <username>` | Remove alert(s) for a player |
| `/formula` | Explains all formulas used |

---

## Quick Start

### 1. Clone and install

```bash
git clone <this-repo>
cd cs2-rating-bot
pip install -r requirements.txt
```

### 2. Get your API keys

**Discord Bot Token**
1. Go to https://discord.com/developers/applications
2. New Application → Bot → Reset Token → copy it

**FACEIT API Key**
1. Go to https://developers.faceit.com
2. Sign in → My Apps → New App → create a Server-side API Key

### 3. Configure

```bash
cp .env.example .env
# Edit .env and paste your tokens
```

### 4. Invite bot to your server

In the Discord Developer Portal → OAuth2 → URL Generator:
- Scopes: `bot`, `applications.commands`
- Bot Permissions: `Send Messages`, `Embed Links`

Open the generated URL and add the bot to your server.

### 5. Run

```bash
python bot.py
```

### Optional helper (Windows)

```powershell
.\run.ps1
# Skip dependency install
.\run.ps1 -SkipInstall
```

The script uses a local `.venv` Python if present.

### Optional helper (macOS/Linux)

```bash
chmod +x run.sh
./run.sh
# Skip dependency install
SKIP_INSTALL=1 ./run.sh
```

### Makefile shortcuts

```bash
make run
make run-skip
```

## Development

- Python: 3.11 (see .python-version)
- Dev deps: `pip install -r requirements.txt -r requirements-dev.txt`
- Lint: `ruff check .`

## Security

See SECURITY.md for reporting guidance.

## License

MIT. See LICENSE.

For instant slash command sync during development, set `GUILD_ID` in `.env`
to your server's ID. Without it, global sync can take up to 1 hour.

---

## How the Ratings Work

### Rating 2.0 & 2.1 Formula

Community reverse-engineered approximation of HLTV's formula:

```
Rating = 0.0073×KAST + 0.3591×KPR − 0.5329×DPR + 0.2372×Impact + 0.0032×ADR + 0.1587

where:
  KPR    = Kills per Round
  DPR    = Deaths per Round
  ADR    = Average Damage per Round
  KAST   = % of rounds with Kill / Assist / Survived / Traded
  Impact = 2.13×KPR + 2.63×MKPR − 0.41
  MKPR   = (rounds with 2+ kills) / total_rounds
```

An average player over an event scores exactly **1.00**.

**Calibration averages used:**

| Version | KPR   | DPR   | ADR  | KAST  | Notes |
|---------|-------|-------|------|-------|-------|
| 2.0     | 0.679 | 0.317 | 79.6 | 74.1% | CS:GO pro averages |
| 2.1     | 0.670 | 0.320 | 76.8 | 73.0% | CS2 / MR12 recalibrated |

**Rating 2.1** also applies a small passive-saver penalty when KAST < 60%
and DPR < 0.25 — approximating HLTV's punishment for saving in lost rounds.

### ≈Rating 3.0 (Approximation)

Real Rating 3.0 requires per-round **win-probability tracking** and full
economy data, neither of which is available through the FACEIT API.

We estimate it in two steps:

**1. Eco-adjustment factor** (proxy)
> HLTV 3.0 weights kills by equipment tier — an AK kill counts more than
> a Glock kill. We approximate this using ADR-per-kill vs HS%:
> high HS% with low ADR suggests pistol/eco frags → slight penalty.
> Factor stays within ×0.94 – ×1.06.

**2. Round Swing estimate** (proxy)
> Real Round Swing measures win-probability delta per kill.
> We estimate high-swing moments using:
> - 1v1 clutch win  → +0.025 per occurrence
> - 1v2 clutch win  → +0.05 per occurrence
> - Triple/quad/penta kills → small additive bonus
> Capped at ±0.12 total.

This is **not** the real HLTV 3.0. Always labelled `≈3.0` in output.

### Missing Stats Fallback

Older FACEIT matches may not have ADR or KAST tracked. When missing:
- **ADR estimated** from kills + assists: `(kills×82 + assists×30) / rounds`
- **KAST estimated** from kill/assist volume, capped at 92%

---

## Sub-rating Bars

```
░░░░░░░░  = 0.70 (well below average)
████████  = 1.30 (elite)
```
Each bar is 8 blocks, linearly scaled between 0.70 and 1.30.

---

## Limitations

- **FACEIT API only** — no Valve matchmaking / Premier support.
- **≈3.0 is an estimate** — do not compare directly to HLTV's official 3.0.
- **ADR/KAST** may be absent in older CS2 matches (pre-late 2024).
- Ratings are calibrated on **pro play** averages. FACEIT ratings will skew
  differently at various ELO ranges.

---

## Project Structure

```
cs2-rating-bot/
├── bot.py              ← Entry point
├── cogs/
│   └── stats.py        ← Discord slash commands
├── utils/
│   ├── faceit.py       ← FACEIT API v4 wrapper
│   └── rating.py       ← Rating formulas + UI helpers
├── requirements.txt
├── .env.example
└── README.md
```
