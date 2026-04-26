"""
Discord Cog — CS2 HLTV Rating Commands
"""

from __future__ import annotations
import os
import asyncio
import statistics
import json
from collections import defaultdict
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, Any

from utils.faceit import FaceitAPI
from utils.rating import (
    PlayerMatchStats,
    calculate_rating_20,
    calculate_rating_21,
    calculate_rating_30_approx,
    rating_color,
    rating_label,
    bar,
)


# ──────────────────────────────────────────────────────────────────────────────
# STAT PARSING
# ──────────────────────────────────────────────────────────────────────────────


def _safe_int(d: dict, *keys, default: int = 0) -> int:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return int(float(v))
            except (ValueError, TypeError):
                pass
    return default


def _safe_float(d: dict, *keys, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return default


def _parse_score(score_str: str) -> int:
    """Return total rounds from a score string like '13:5' or '16-8'."""
    try:
        parts = [int(x) for x in score_str.replace(":", " ").replace("-", " ").split()]
        return sum(parts) if parts else 24
    except Exception:
        return 24


def _map_label(raw: str) -> str:
    if not raw:
        return "Unknown"
    name = raw.replace("de_", "").replace("workshop/", "")
    return name.replace("_", " ").title()


def _sparkline(values: list[float]) -> str:
    if not values:
        return "—"
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return blocks[len(blocks) // 2] * len(values)
    out = []
    for v in values:
        idx = int(round((v - lo) / (hi - lo) * (len(blocks) - 1)))
        out.append(blocks[max(0, min(len(blocks) - 1, idx))])
    return "".join(out)


def _consistency_score(ratings: list[float]) -> int:
    if len(ratings) < 2:
        return 100
    mean = sum(ratings) / len(ratings)
    if mean <= 0:
        return 0
    cv = statistics.pstdev(ratings) / mean
    score = 100 - cv * 120
    return max(0, min(100, int(round(score))))


def _role_profile(
    agg: PlayerMatchStats, avg_rating: float, n_maps: int
) -> tuple[str, str]:
    rounds = max(agg.total_rounds, 1)
    kpr = agg.kills / rounds
    dpr = agg.deaths / rounds
    apr = agg.assists / rounds
    clutch_per_map = (agg.clutch_1v1 + agg.clutch_1v2) / max(n_maps, 1)

    if kpr >= 0.8 and agg.adr >= 82:
        return "Entry / Fragger", "High opening frag volume and damage output."
    if apr >= 0.18 and agg.kast >= 72:
        return "Support / Trader", "Strong assist rate with stable teamplay rounds."
    if dpr <= 0.62 and agg.kast >= 74 and clutch_per_map >= 0.3:
        return "Anchor / Closer", "Survives often and converts late-round situations."
    if avg_rating >= 1.1 and dpr <= 0.68:
        return "Star Rifler", "Above-average impact with efficient deaths."
    return "Balanced Rifler", "Even profile without a heavy role skew."


def _avg_rating(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(r["r21"] for r in rows) / len(rows)


def _consistency_rows(rows: list[dict[str, Any]]) -> int:
    return _consistency_score([r["r21"] for r in rows])


def _bot_root_dir() -> str:
    return os.path.dirname(os.path.dirname(__file__))


def _weekly_store_path() -> str:
    return os.path.join(_bot_root_dir(), "data", "weekly_reports.json")


def _load_weekly_subscriptions() -> dict[str, dict[str, Any]]:
    path = _weekly_store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_weekly_subscriptions(data: dict[str, dict[str, Any]]):
    path = _weekly_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def parse_player_stats(ps: dict, total_rounds: int) -> PlayerMatchStats:
    """
    Convert raw FACEIT API player_stats dict → PlayerMatchStats.
    Handles varying key names across different FACEIT API versions.
    """
    return PlayerMatchStats(
        kills=_safe_int(ps, "Kills"),
        deaths=_safe_int(ps, "Deaths"),
        assists=_safe_int(ps, "Assists"),
        total_rounds=total_rounds,
        adr=_safe_float(ps, "ADR", "Average Damage per Round"),
        kast=_safe_float(ps, "KAST", "KAST %"),
        double_kills=_safe_int(ps, "Double Kills", "2k"),
        triple_kills=_safe_int(ps, "Triple Kills", "3k"),
        quad_kills=_safe_int(ps, "Quadro Kills", "4k"),
        penta_kills=_safe_int(ps, "Penta Kills", "5k"),
        clutch_1v1=_safe_int(ps, "1v1Wins", "1v1 Wins"),
        clutch_1v2=_safe_int(ps, "1v2Wins", "1v2 Wins"),
        headshots=_safe_int(ps, "Headshots"),
        hs_pct=_safe_float(ps, "Headshots %", "Headshot %"),
        flash_assists=_safe_int(ps, "Flash Assists"),
        mvps=_safe_int(ps, "MVPs"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# EMBED BUILDERS
# ──────────────────────────────────────────────────────────────────────────────


def _kd_str(s: PlayerMatchStats) -> str:
    kd = round(s.kills / max(s.deaths, 1), 2)
    return f"{s.kills}/{s.deaths}/{s.assists} ({kd})"


def _mk_str(s: PlayerMatchStats) -> str:
    parts = []
    if s.penta_kills:
        parts.append(f"5K×{s.penta_kills}")
    if s.quad_kills:
        parts.append(f"4K×{s.quad_kills}")
    if s.triple_kills:
        parts.append(f"3K×{s.triple_kills}")
    if s.double_kills:
        parts.append(f"2K×{s.double_kills}")
    return "  ".join(parts) if parts else "—"


def _clutch_str(s: PlayerMatchStats) -> str:
    if s.clutch_1v1 or s.clutch_1v2:
        return f"1v1: {s.clutch_1v1}W  |  1v2: {s.clutch_1v2}W"
    return "—"


def build_match_embed(
    username: str, map_name: str, score: str, s: PlayerMatchStats
) -> discord.Embed:
    r20 = calculate_rating_20(s)
    r21 = calculate_rating_21(s)
    r30 = calculate_rating_30_approx(s)

    embed = discord.Embed(
        title=f"📊  {username}  ·  {map_name}",
        description=(
            f"Score **{score}**  ·  {s.total_rounds} rounds  ·  "
            f"{rating_label(r21['rating'])}"
        ),
        color=rating_color(r21["rating"]),
    )

    # ── Ratings ──────────────────────────────────────────────────────────────
    embed.add_field(
        name="🏆  Ratings",
        value=(
            f"```\n"
            f"2.0  {r20['rating']:.2f}  {bar(r20['rating'])}\n"
            f"2.1  {r21['rating']:.2f}  {bar(r21['rating'])}\n"
            f"≈3.0 {r30['rating']:.2f}  {bar(r30['rating'])}\n"
            f"```"
        ),
        inline=False,
    )

    # ── Core stats ───────────────────────────────────────────────────────────
    embed.add_field(
        name="📈  Core",
        value=(
            f"**K/D/A** `{_kd_str(s)}`\n"
            f"**ADR**   `{r21['adr']}`\n"
            f"**KAST**  `{r21['kast']}%`\n"
            f"**HS%**   `{s.hs_pct:.0f}%`"
        ),
        inline=True,
    )

    # ── Per-round ────────────────────────────────────────────────────────────
    sub = r21["sub_ratings"]
    embed.add_field(
        name="🎯  Per Round",
        value=(
            f"**KPR**     `{r21['kpr']}`\n"
            f"**DPR**     `{r21['dpr']}`\n"
            f"**Impact**  `{r21['impact']}`\n"
            f"**MVPs**    `{s.mvps}`"
        ),
        inline=True,
    )

    # ── Sub-ratings (2.1) ────────────────────────────────────────────────────
    embed.add_field(
        name="🔬  Sub-Ratings (2.1)",
        value=(
            f"Kill      `{sub['kill']}`  {bar(sub['kill'])}\n"
            f"Survival  `{sub['survival']}`  {bar(sub['survival'])}\n"
            f"Damage    `{sub['damage']}`  {bar(sub['damage'])}\n"
            f"KAST      `{sub['kast']}`  {bar(sub['kast'])}"
        ),
        inline=False,
    )

    # ── Multi-kills & Clutches ───────────────────────────────────────────────
    embed.add_field(name="💥  Multi-kills", value=_mk_str(s), inline=True)
    embed.add_field(name="🤝  Clutches", value=_clutch_str(s), inline=True)

    # ── Rating 3.0 breakdown ─────────────────────────────────────────────────
    embed.add_field(
        name="🔄  ≈3.0 Breakdown",
        value=(
            f"Base (2.1)    `{r30['base_21']:.2f}`\n"
            f"Eco factor    `×{r30['eco_factor']:.3f}`\n"
            f"Swing bonus   `+{r30['swing_bonus']:.3f}`\n"
            f"**Result**    `{r30['rating']:.2f}`"
        ),
        inline=True,
    )

    embed.set_footer(
        text=(
            "⚠️  Ratings are approximations.  "
            "≈3.0 Round Swing is estimated — not real HLTV data.  "
            "Formula: community reverse-engineered 2.0 base."
        )
    )
    return embed


def build_summary_embed(
    username: str, n_maps: int, agg: PlayerMatchStats
) -> discord.Embed:
    r20 = calculate_rating_20(agg)
    r21 = calculate_rating_21(agg)
    r30 = calculate_rating_30_approx(agg)
    sub = r21["sub_ratings"]

    embed = discord.Embed(
        title=f"📊  {username}  ·  Last {n_maps} map{'s' if n_maps > 1 else ''}",
        description=f"Aggregated over {n_maps} map(s)  ·  {rating_label(r21['rating'])}",
        color=rating_color(r21["rating"]),
    )
    embed.add_field(
        name="🏆  Avg Ratings",
        value=(
            f"```\n"
            f"2.0  {r20['rating']:.2f}  {bar(r20['rating'])}\n"
            f"2.1  {r21['rating']:.2f}  {bar(r21['rating'])}\n"
            f"≈3.0 {r30['rating']:.2f}  {bar(r30['rating'])}\n"
            f"```"
        ),
        inline=False,
    )
    embed.add_field(
        name="📈  Averages",
        value=(
            f"**K/D/A**  `{_kd_str(agg)}`\n"
            f"**KPR**    `{r21['kpr']}`\n"
            f"**ADR**    `{r21['adr']}`\n"
            f"**KAST**   `{r21['kast']}%`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🔬  Sub-Ratings (2.1)",
        value=(
            f"Kill      `{sub['kill']}`  {bar(sub['kill'])}\n"
            f"Survival  `{sub['survival']}`  {bar(sub['survival'])}\n"
            f"Damage    `{sub['damage']}`  {bar(sub['damage'])}\n"
            f"KAST      `{sub['kast']}`  {bar(sub['kast'])}"
        ),
        inline=True,
    )
    embed.set_footer(text="Ratings are approximations based on FACEIT API stats.")
    return embed


# ──────────────────────────────────────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────────────────────────────────────


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.faceit = FaceitAPI(os.getenv("FACEIT_API_KEY", ""))
        self.weekly_subscriptions: dict[str, dict[str, Any]] = (
            _load_weekly_subscriptions()
        )
        self._weekly_last_sent: dict[str, str] = {}
        self.weekly_report_loop.start()

    async def cog_unload(self):
        self.weekly_report_loop.cancel()
        await self.faceit.close()

    # ── /rating ───────────────────────────────────────────────────────────────

    @app_commands.command(
        name="rating",
        description="HLTV-style rating for a FACEIT player's recent match(es)",
    )
    @app_commands.describe(
        username="FACEIT nickname",
        maps="Number of recent maps to average (1–15, default 1)",
    )
    async def rating(
        self,
        interaction: discord.Interaction,
        username: str,
        maps: Optional[int] = 1,
    ):
        await interaction.response.defer(thinking=True)
        maps = max(1, min(maps or 1, 15))
        try:
            await self._cmd_rating(interaction, username, maps)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /matchrating ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="matchrating",
        description="HLTV-style rating for a specific FACEIT match ID",
    )
    @app_commands.describe(
        username="FACEIT nickname",
        match_id="FACEIT match ID (from match URL or history)",
    )
    async def matchrating(
        self,
        interaction: discord.Interaction,
        username: str,
        match_id: str,
    ):
        await interaction.response.defer(thinking=True)
        try:
            await self._send_match(interaction, username, match_id)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /analyze ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="analyze",
        description="Analyze last N maps: trend, consistency, maps, role profile",
    )
    @app_commands.describe(
        username="FACEIT nickname",
        maps="Number of recent maps to analyze (3–20, default 10)",
    )
    async def analyze(
        self,
        interaction: discord.Interaction,
        username: str,
        maps: Optional[int] = 10,
    ):
        await interaction.response.defer(thinking=True)
        maps = max(3, min(maps or 10, 20))
        try:
            await self._cmd_analyze(interaction, username, maps)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /compare ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="compare",
        description="Compare two FACEIT players side-by-side over recent maps",
    )
    @app_commands.describe(
        username_a="First FACEIT nickname",
        username_b="Second FACEIT nickname",
        maps="Number of recent maps to compare (3–20, default 10)",
    )
    async def compare(
        self,
        interaction: discord.Interaction,
        username_a: str,
        username_b: str,
        maps: Optional[int] = 10,
    ):
        await interaction.response.defer(thinking=True)
        maps = max(3, min(maps or 10, 20))
        try:
            await self._cmd_compare(interaction, username_a, username_b, maps)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /maps ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="maps",
        description="Per-map rating breakdown for a FACEIT player",
    )
    @app_commands.describe(
        username="FACEIT nickname",
        maps="Number of recent maps to include (3–30, default 15)",
    )
    async def maps_breakdown(
        self,
        interaction: discord.Interaction,
        username: str,
        maps: Optional[int] = 15,
    ):
        await interaction.response.defer(thinking=True)
        maps = max(3, min(maps or 15, 30))
        try:
            await self._cmd_maps(interaction, username, maps)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /session ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="session",
        description="Compare recent form vs baseline maps (hot/cold session)",
    )
    @app_commands.describe(
        username="FACEIT nickname",
        recent_maps="Recent sample size (default 5)",
        baseline_maps="Previous baseline sample size (default 20)",
    )
    async def session(
        self,
        interaction: discord.Interaction,
        username: str,
        recent_maps: Optional[int] = 5,
        baseline_maps: Optional[int] = 20,
    ):
        await interaction.response.defer(thinking=True)
        recent_maps = max(3, min(recent_maps or 5, 15))
        baseline_maps = max(5, min(baseline_maps or 20, 40))
        try:
            await self._cmd_session(interaction, username, recent_maps, baseline_maps)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /weeklyreport ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="weeklyreport",
        description="Generate a weekly report card now",
    )
    @app_commands.describe(
        username="FACEIT nickname",
        maps="Number of recent maps to include (default 10)",
    )
    async def weeklyreport(
        self,
        interaction: discord.Interaction,
        username: str,
        maps: Optional[int] = 10,
    ):
        await interaction.response.defer(thinking=True)
        maps = max(5, min(maps or 10, 30))
        try:
            embed = await self._build_weekly_report_embed(username, maps)
            await interaction.followup.send(embed=embed)
        except Exception as exc:
            await interaction.followup.send(f"❌  {exc}", ephemeral=True)

    # ── /weeklysubscribe ──────────────────────────────────────────────────────

    @app_commands.command(
        name="weeklysubscribe",
        description="Enable automatic weekly report in this channel",
    )
    @app_commands.describe(
        username="FACEIT nickname for this server's weekly report",
        channel="Target channel (defaults to current channel)",
    )
    async def weeklysubscribe(
        self,
        interaction: discord.Interaction,
        username: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌  This command can only be used in a server.", ephemeral=True
            )
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌  You need Manage Server permission.", ephemeral=True
            )
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌  Weekly reports require a text channel.", ephemeral=True
            )
            return

        gid = str(interaction.guild.id)
        self.weekly_subscriptions[gid] = {
            "channel_id": target_channel.id,
            "username": username,
            "maps": 10,
        }
        _save_weekly_subscriptions(self.weekly_subscriptions)
        await interaction.response.send_message(
            f"✅  Weekly report enabled for `{username}` in {target_channel.mention}. "
            "Runs every Monday at 09:00 UTC."
        )

    # ── /weeklyunsubscribe ────────────────────────────────────────────────────

    @app_commands.command(
        name="weeklyunsubscribe",
        description="Disable automatic weekly report for this server",
    )
    async def weeklyunsubscribe(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌  This command can only be used in a server.", ephemeral=True
            )
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌  You need Manage Server permission.", ephemeral=True
            )
            return

        gid = str(interaction.guild.id)
        if gid in self.weekly_subscriptions:
            del self.weekly_subscriptions[gid]
            _save_weekly_subscriptions(self.weekly_subscriptions)
            await interaction.response.send_message(
                "✅  Weekly report disabled for this server."
            )
            return
        await interaction.response.send_message(
            "ℹ️  Weekly report was not configured for this server.", ephemeral=True
        )

    # ── /formula ──────────────────────────────────────────────────────────────

    @app_commands.command(
        name="formula",
        description="Explains the rating formulas used by this bot",
    )
    async def formula(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📐  Rating Formula Reference",
            color=0x5865F2,
        )
        embed.add_field(
            name="Rating 2.0 / 2.1 Formula",
            value=(
                "```\n"
                "Rating = 0.0073×KAST + 0.3591×KPR\n"
                "       − 0.5329×DPR + 0.2372×Impact\n"
                "       + 0.0032×ADR + 0.1587\n\n"
                "Impact = 2.13×KPR + 2.63×MKPR − 0.41\n"
                "MKPR   = (2K+3K+4K+5K rounds) / total rounds\n"
                "```"
            ),
            inline=False,
        )
        embed.add_field(
            name="Calibration Averages",
            value=(
                "```\n"
                "         KPR    DPR    ADR    KAST\n"
                "2.0      0.679  0.317  79.6   74.1%\n"
                "2.1      0.670  0.320  76.8   73.0%\n"
                "(CS2/MR12 recalibrated)\n"
                "```"
            ),
            inline=False,
        )
        embed.add_field(
            name="≈3.0 Modifiers (Estimated)",
            value=(
                "**Eco-adjustment** (proxy via ADR-per-kill vs HS%)\n"
                "> High HS% + low ADR suggests eco-padding → slight penalty.\n\n"
                "**Round Swing** (estimated from clutches + multi-kills)\n"
                "> Clutch 1v1 win: +0.025/round  |  1v2 win: +0.05/round\n"
                "> Triple: ×0.015  Quad: ×0.03  Penta: ×0.06"
            ),
            inline=False,
        )
        embed.add_field(
            name="Sub-rating bars",
            value="`░░░░░░░░` = 0.70  →  `████████` = 1.30  (1.00 = average)",
            inline=False,
        )
        embed.set_footer(
            text=(
                "Formula is a community reverse-engineering of HLTV 2.0. "
                "HLTV's exact weights are proprietary."
            )
        )
        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────────────────────────────────

    async def _resolve_player(self, username: str) -> tuple[str, str]:
        """Return (player_id, resolved_nickname) or raise."""
        player = await self.faceit.get_player(username)
        if not player:
            raise ValueError(f"Player `{username}` not found on FACEIT (CS2).")
        return player["player_id"], player.get("nickname", username)

    async def _cmd_rating(
        self, interaction: discord.Interaction, username: str, maps: int
    ):
        player_id, nickname = await self._resolve_player(username)
        history = await self.faceit.get_match_history(player_id, limit=maps)

        if not history or not history.get("items"):
            await interaction.followup.send(
                f"❌  No recent CS2 matches found for `{nickname}`."
            )
            return

        items = history["items"]

        if maps == 1:
            await self._send_match(
                interaction, nickname, items[0]["match_id"], player_id=player_id
            )
        else:
            await self._send_aggregated(interaction, nickname, player_id, items)

    async def _collect_recent_player_maps(
        self, player_id: str, limit: int
    ) -> list[dict[str, Any]]:
        history = await self.faceit.get_match_history(player_id, limit=limit)
        if not history or not history.get("items"):
            return []

        match_ids = [
            it.get("match_id") for it in history["items"] if it.get("match_id")
        ]
        if not match_ids:
            return []

        sem = asyncio.Semaphore(5)

        async def _fetch(mid: str):
            async with sem:
                try:
                    return mid, await self.faceit.get_match_stats(mid)
                except Exception:
                    return mid, None

        fetched = await asyncio.gather(*[_fetch(mid) for mid in match_ids])
        rows: list[dict[str, Any]] = []

        for mid, stats_data in fetched:
            if not stats_data:
                continue
            for round_data in stats_data.get("rounds", []):
                rs = round_data.get("round_stats", {})
                score = rs.get("Score", "12-12")
                total_rds = _parse_score(score)

                player_raw = None
                for team in round_data.get("teams", []):
                    for p in team.get("players", []):
                        if p.get("player_id") == player_id:
                            player_raw = p
                            break
                    if player_raw:
                        break

                if not player_raw:
                    continue

                s = parse_player_stats(player_raw.get("player_stats", {}), total_rds)
                r21 = calculate_rating_21(s)
                rows.append(
                    {
                        "match_id": mid,
                        "map": _map_label(rs.get("Map", "Unknown")),
                        "score": score,
                        "stats": s,
                        "r21": r21["rating"],
                    }
                )

        return rows

    def _aggregate_rows(self, rows: list[dict[str, Any]]) -> PlayerMatchStats:
        agg = dict(
            kills=0,
            deaths=0,
            assists=0,
            total_rounds=0,
            adrs=[],
            kasts=[],
            hs_pcts=[],
            double_kills=0,
            triple_kills=0,
            quad_kills=0,
            penta_kills=0,
            clutch_1v1=0,
            clutch_1v2=0,
            headshots=0,
            mvps=0,
            flash_assists=0,
        )

        for row in rows:
            s: PlayerMatchStats = row["stats"]
            agg["kills"] += s.kills
            agg["deaths"] += s.deaths
            agg["assists"] += s.assists
            agg["total_rounds"] += s.total_rounds
            agg["double_kills"] += s.double_kills
            agg["triple_kills"] += s.triple_kills
            agg["quad_kills"] += s.quad_kills
            agg["penta_kills"] += s.penta_kills
            agg["clutch_1v1"] += s.clutch_1v1
            agg["clutch_1v2"] += s.clutch_1v2
            agg["headshots"] += s.headshots
            agg["mvps"] += s.mvps
            agg["flash_assists"] += s.flash_assists
            if s.adr > 0:
                agg["adrs"].append(s.adr)
            if s.kast > 0:
                agg["kasts"].append(s.kast)
            if s.hs_pct > 0:
                agg["hs_pcts"].append(s.hs_pct)

        return PlayerMatchStats(
            kills=agg["kills"],
            deaths=agg["deaths"],
            assists=agg["assists"],
            total_rounds=agg["total_rounds"],
            adr=sum(agg["adrs"]) / len(agg["adrs"]) if agg["adrs"] else 0,
            kast=sum(agg["kasts"]) / len(agg["kasts"]) if agg["kasts"] else 0,
            hs_pct=sum(agg["hs_pcts"]) / len(agg["hs_pcts"]) if agg["hs_pcts"] else 0,
            double_kills=agg["double_kills"],
            triple_kills=agg["triple_kills"],
            quad_kills=agg["quad_kills"],
            penta_kills=agg["penta_kills"],
            clutch_1v1=agg["clutch_1v1"],
            clutch_1v2=agg["clutch_1v2"],
            headshots=agg["headshots"],
            flash_assists=agg["flash_assists"],
            mvps=agg["mvps"],
        )

    async def _cmd_analyze(
        self, interaction: discord.Interaction, username: str, maps: int
    ):
        player_id, nickname = await self._resolve_player(username)
        rows = await self._collect_recent_player_maps(player_id, maps)
        if not rows:
            await interaction.followup.send(
                f"❌  No recent CS2 map stats found for `{nickname}`."
            )
            return

        ratings = [row["r21"] for row in rows]
        trend_delta = ratings[0] - ratings[-1] if len(ratings) >= 2 else 0.0
        spark = _sparkline(list(reversed(ratings)))
        consistency = _consistency_score(ratings)

        agg = self._aggregate_rows(rows)
        avg21 = calculate_rating_21(agg)["rating"]
        role, role_reason = _role_profile(agg, avg21, len(rows))

        grouped: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            grouped[row["map"]].append(row["r21"])
        ranked_maps = sorted(
            ((m, sum(v) / len(v), len(v)) for m, v in grouped.items()),
            key=lambda x: x[1],
            reverse=True,
        )
        best = ranked_maps[0]
        worst = ranked_maps[-1]

        embed = discord.Embed(
            title=f"🧠  Analysis · {nickname}",
            description=f"Last {len(rows)} map(s) on FACEIT CS2",
            color=rating_color(avg21),
        )
        embed.add_field(
            name="📈 Trend",
            value=(
                f"2.1 Avg: `{avg21:.2f}`\n"
                f"Delta: `{trend_delta:+.2f}` (newest vs oldest)\n"
                f"Spark: `{spark}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎯 Consistency + Role",
            value=(
                f"Consistency: `{consistency}/100`\nRole: **{role}**\n{role_reason}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗺️ Best / Worst Map",
            value=(
                f"Best: **{best[0]}** · `{best[1]:.2f}` over {best[2]} map(s)\n"
                f"Worst: **{worst[0]}** · `{worst[1]:.2f}` over {worst[2]} map(s)"
            ),
            inline=False,
        )
        embed.set_footer(text="Trend sparkline reads left→right (oldest→newest).")
        await interaction.followup.send(embed=embed)

    async def _cmd_compare(
        self,
        interaction: discord.Interaction,
        username_a: str,
        username_b: str,
        maps: int,
    ):
        p1, n1 = await self._resolve_player(username_a)
        p2, n2 = await self._resolve_player(username_b)

        rows1, rows2 = await asyncio.gather(
            self._collect_recent_player_maps(p1, maps),
            self._collect_recent_player_maps(p2, maps),
        )
        if not rows1:
            await interaction.followup.send(
                f"❌  No recent CS2 map stats found for `{n1}`."
            )
            return
        if not rows2:
            await interaction.followup.send(
                f"❌  No recent CS2 map stats found for `{n2}`."
            )
            return

        agg1 = self._aggregate_rows(rows1)
        agg2 = self._aggregate_rows(rows2)
        r1 = calculate_rating_21(agg1)["rating"]
        r2 = calculate_rating_21(agg2)["rating"]
        c1 = _consistency_score([x["r21"] for x in rows1])
        c2 = _consistency_score([x["r21"] for x in rows2])

        kd1 = agg1.kills / max(agg1.deaths, 1)
        kd2 = agg2.kills / max(agg2.deaths, 1)

        def lead(a: float, b: float) -> str:
            if abs(a - b) < 1e-9:
                return "Tie"
            return n1 if a > b else n2

        embed = discord.Embed(
            title=f"⚖️  Compare · {n1} vs {n2}",
            description=f"Recent sample: {len(rows1)} vs {len(rows2)} map(s)",
            color=0x5865F2,
        )
        embed.add_field(
            name=n1,
            value=(
                f"2.1 Avg `{r1:.2f}`\n"
                f"K/D `{kd1:.2f}`\n"
                f"ADR `{agg1.adr:.1f}`\n"
                f"KAST `{agg1.kast:.1f}%`\n"
                f"Consistency `{c1}`"
            ),
            inline=True,
        )
        embed.add_field(
            name=n2,
            value=(
                f"2.1 Avg `{r2:.2f}`\n"
                f"K/D `{kd2:.2f}`\n"
                f"ADR `{agg2.adr:.1f}`\n"
                f"KAST `{agg2.kast:.1f}%`\n"
                f"Consistency `{c2}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="🏁 Leaders",
            value=(
                f"Rating 2.1: **{lead(r1, r2)}**\n"
                f"K/D: **{lead(kd1, kd2)}**\n"
                f"ADR: **{lead(agg1.adr, agg2.adr)}**\n"
                f"KAST: **{lead(agg1.kast, agg2.kast)}**\n"
                f"Consistency: **{lead(c1, c2)}**"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed)

    async def _cmd_maps(
        self, interaction: discord.Interaction, username: str, maps: int
    ):
        player_id, nickname = await self._resolve_player(username)
        rows = await self._collect_recent_player_maps(player_id, maps)
        if not rows:
            await interaction.followup.send(
                f"❌  No recent CS2 map stats found for `{nickname}`."
            )
            return

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["map"]].append(row)

        lines = []
        for map_name, g in grouped.items():
            agg = self._aggregate_rows(g)
            avg = calculate_rating_21(agg)["rating"]
            kd = agg.kills / max(agg.deaths, 1)
            lines.append(
                (avg, f"{map_name:<10}  r2.1 {avg:.2f}  KD {kd:.2f}  ({len(g)} map)")
            )

        lines.sort(key=lambda x: x[0], reverse=True)
        body = "\n".join(x[1] for x in lines[:15])

        embed = discord.Embed(
            title=f"🗺️  Map Breakdown · {nickname}",
            description=f"Computed from last {len(rows)} map(s)",
            color=0x2ECC71,
        )
        embed.add_field(
            name="Per-map Rating 2.1", value=f"```\n{body}\n```", inline=False
        )
        await interaction.followup.send(embed=embed)

    async def _cmd_session(
        self,
        interaction: discord.Interaction,
        username: str,
        recent_maps: int,
        baseline_maps: int,
    ):
        player_id, nickname = await self._resolve_player(username)
        rows = await self._collect_recent_player_maps(
            player_id, recent_maps + baseline_maps
        )
        if len(rows) < recent_maps + 2:
            await interaction.followup.send(
                f"❌  Not enough recent data for `{nickname}`. Need at least {recent_maps + 2} maps."
            )
            return

        recent = rows[:recent_maps]
        baseline = rows[recent_maps : recent_maps + baseline_maps]
        if len(baseline) < 3:
            await interaction.followup.send(
                f"❌  Baseline sample too small for `{nickname}`. Try lower `recent_maps`."
            )
            return

        recent_avg = _avg_rating(recent)
        base_avg = _avg_rating(baseline)
        delta = recent_avg - base_avg
        status = (
            "🔥 Hot" if delta >= 0.08 else "🧊 Cold" if delta <= -0.08 else "➖ Stable"
        )
        recent_cons = _consistency_rows(recent)
        base_cons = _consistency_rows(baseline)
        confidence = min(100, int(round((len(recent) * 7 + len(baseline) * 3))))
        spark = _sparkline(list(reversed([r["r21"] for r in recent])))

        embed = discord.Embed(
            title=f"📅  Session Check · {nickname}",
            description=(
                f"Recent `{len(recent)}` map(s) vs previous `{len(baseline)}` map(s)\n"
                f"Status: **{status}**"
            ),
            color=rating_color(recent_avg),
        )
        embed.add_field(
            name="2.1 Trend",
            value=(
                f"Recent Avg: `{recent_avg:.2f}`\n"
                f"Baseline Avg: `{base_avg:.2f}`\n"
                f"Delta: `{delta:+.2f}`\n"
                f"Spark: `{spark}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Stability",
            value=(
                f"Recent Consistency: `{recent_cons}/100`\n"
                f"Baseline Consistency: `{base_cons}/100`\n"
                f"Confidence: `{confidence}/100`"
            ),
            inline=True,
        )
        embed.set_footer(
            text="Use this for form tracking, not absolute skill estimation."
        )
        await interaction.followup.send(embed=embed)

    async def _build_weekly_report_embed(
        self, username: str, maps: int = 10
    ) -> discord.Embed:
        player_id, nickname = await self._resolve_player(username)
        rows = await self._collect_recent_player_maps(player_id, maps)
        if not rows:
            raise ValueError(f"No recent CS2 map stats found for `{nickname}`.")

        agg = self._aggregate_rows(rows)
        avg = calculate_rating_21(agg)["rating"]
        consistency = _consistency_rows(rows)
        role, _ = _role_profile(agg, avg, len(rows))

        grouped: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            grouped[row["map"]].append(row["r21"])
        ranked_maps = sorted(
            ((m, sum(v) / len(v), len(v)) for m, v in grouped.items()),
            key=lambda x: x[1],
            reverse=True,
        )
        best = ranked_maps[0]
        worst = ranked_maps[-1]

        kdr = agg.kills / max(agg.deaths, 1)
        spark = _sparkline(list(reversed([r["r21"] for r in rows[:10]])))

        embed = discord.Embed(
            title=f"🗓️ Weekly Report · {nickname}",
            description=f"Last {len(rows)} map(s) · FACEIT CS2",
            color=rating_color(avg),
        )
        embed.add_field(
            name="Overview",
            value=(
                f"2.1 Avg: `{avg:.2f}`\n"
                f"K/D: `{kdr:.2f}`\n"
                f"ADR: `{agg.adr:.1f}`\n"
                f"KAST: `{agg.kast:.1f}%`\n"
                f"Consistency: `{consistency}/100`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Map & Role",
            value=(
                f"Best: **{best[0]}** `{best[1]:.2f}`\n"
                f"Worst: **{worst[0]}** `{worst[1]:.2f}`\n"
                f"Role: **{role}**\n"
                f"Trend: `{spark}`"
            ),
            inline=True,
        )
        embed.set_footer(text="Auto report runs Mondays 09:00 UTC when subscribed.")
        return embed

    @tasks.loop(minutes=30)
    async def weekly_report_loop(self):
        now = datetime.now(timezone.utc)
        # Monday 09:00–09:29 UTC window
        if not (now.weekday() == 0 and now.hour == 9):
            return

        date_key = now.strftime("%Y-%m-%d")
        for guild_id, cfg in list(self.weekly_subscriptions.items()):
            if self._weekly_last_sent.get(guild_id) == date_key:
                continue

            channel_id = int(cfg.get("channel_id", 0))
            username = str(cfg.get("username", "")).strip()
            maps = int(cfg.get("maps", 10))
            if not channel_id or not username:
                continue

            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue

            try:
                embed = await self._build_weekly_report_embed(username, maps)
                await channel.send(embed=embed)
                self._weekly_last_sent[guild_id] = date_key
            except Exception:
                continue

    @weekly_report_loop.before_loop
    async def _before_weekly_report_loop(self):
        await self.bot.wait_until_ready()

    async def _send_match(
        self,
        interaction: discord.Interaction,
        username: str,
        match_id: str,
        player_id: Optional[str] = None,
    ):
        if player_id is None:
            player_id, username = await self._resolve_player(username)

        stats_data = await self.faceit.get_match_stats(match_id)
        if not stats_data:
            await interaction.followup.send(
                f"❌  No stats found for match `{match_id}`. "
                "The match may not have stats tracked yet."
            )
            return

        embeds: list[discord.Embed] = []

        for round_data in stats_data.get("rounds", []):
            rs = round_data.get("round_stats", {})
            map_name = rs.get("Map", "Unknown Map")
            score = rs.get("Score", "?-?")
            total_rds = _parse_score(score)

            # Find this player
            player_raw = None
            for team in round_data.get("teams", []):
                for p in team.get("players", []):
                    if p.get("player_id") == player_id:
                        player_raw = p
                        break

            if player_raw is None:
                continue

            s = parse_player_stats(player_raw.get("player_stats", {}), total_rds)
            embeds.append(build_match_embed(username, map_name, score, s))

        if not embeds:
            await interaction.followup.send(
                f"❌  `{username}` was not found in match `{match_id}`."
            )
            return

        # Discord allows max 10 embeds per message; a BO3 has at most 3 maps
        await interaction.followup.send(embeds=embeds[:5])

    async def _send_aggregated(
        self,
        interaction: discord.Interaction,
        username: str,
        player_id: str,
        items: list,
    ):
        """Fetch stats for multiple matches and aggregate."""
        requested_match_ids = [it.get("match_id") for it in items if it.get("match_id")]
        rows = await self._collect_recent_player_maps(
            player_id, len(requested_match_ids)
        )
        n = len(rows)
        if n == 0:
            await interaction.followup.send("❌  Could not retrieve stats for any map.")
            return

        match_id_set = set(requested_match_ids)
        filtered_rows = [r for r in rows if r["match_id"] in match_id_set]
        if not filtered_rows:
            await interaction.followup.send("❌  Could not retrieve stats for any map.")
            return

        agg_stats = self._aggregate_rows(filtered_rows)

        embed = build_summary_embed(username, len(filtered_rows), agg_stats)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
