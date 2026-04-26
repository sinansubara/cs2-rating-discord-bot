"""
HLTV-style Rating Calculations for CS2
======================================

Rating 2.0  — CS:GO era formula, community reverse-engineered.
             Formula: 0.0073*KAST + 0.3591*KPR - 0.5329*DPR + 0.2372*Impact + 0.0032*ADR + 0.1587
             Impact  = 2.13*KPR + 2.63*MKPR - 0.41
             Average player over an event = 1.00

Rating 2.1  — Same formula, CS2-adjusted averages (MR12 format, 26-dmg assists).
             Adds a passive-saver penalty: if KAST < 60 and DPR < 0.25, mild deduction.

Rating 3.0  — Approximate only.
             Real 3.0 requires per-round win-probability data (Round Swing) and full
             economy tracking, neither of which is available via the FACEIT API.
             We estimate using:
               • Eco-adjustment factor — uses ADR-per-kill vs HS% as a rough proxy
                 for whether kills came from pistol/eco rounds (less valuable in 3.0).
               • Round Swing estimate — clutch wins (1v1, 1v2) and multi-kills serve
                 as proxies for high-swing moments.
             Label clearly as "≈3.0" in all output.

References:
  https://www.hltv.org/news/42485/introducing-rating-30
  https://www.hltv.org/news/40051/introducing-rating-21
  Community formula thread: https://www.reddit.com/r/GlobalOffensive/comments/5ymg3x/
"""

from __future__ import annotations
from dataclasses import dataclass, field


# ──────────────────────────────────────────────────────────────────────────────
# DATA CLASS
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerMatchStats:
    """All per-match stats needed for rating calculation."""
    # Core (always available from FACEIT API)
    kills: int
    deaths: int
    assists: int
    total_rounds: int

    # Semi-reliable (CS2 matches from ~2024+)
    adr: float = 0.0     # Average Damage per Round
    kast: float = 0.0    # % of rounds with Kill/Assist/Survived/Traded (0-100)

    # Multi-kills
    double_kills: int = 0
    triple_kills: int = 0
    quad_kills: int = 0
    penta_kills: int = 0

    # Clutches
    clutch_1v1: int = 0
    clutch_1v2: int = 0

    # Misc
    headshots: int = 0
    hs_pct: float = 0.0   # 0-100
    flash_assists: int = 0
    mvps: int = 0


# ──────────────────────────────────────────────────────────────────────────────
# CALIBRATION CONSTANTS
# Each tuple is (avg_kpr, avg_dpr, avg_adr, avg_kast_pct)
# ──────────────────────────────────────────────────────────────────────────────

# Rating 2.0 — calibrated on CS:GO pro data
_CAL_20 = dict(avg_kpr=0.679, avg_dpr=0.317, avg_adr=79.6, avg_kast=74.1)

# Rating 2.1 — CS2 / MR12 recalibrated (lower ADR, slightly lower KAST)
_CAL_21 = dict(avg_kpr=0.670, avg_dpr=0.320, avg_adr=76.8, avg_kast=73.0)

# Rating 3.0 base (same calibration as 2.1, eco-adjustment applied on top)
_CAL_30 = _CAL_21


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 3.5) -> float:
    return max(lo, min(hi, v))


def _kpr(s: PlayerMatchStats) -> float:
    return s.kills / max(s.total_rounds, 1)

def _dpr(s: PlayerMatchStats) -> float:
    return s.deaths / max(s.total_rounds, 1)

def _mkpr(s: PlayerMatchStats) -> float:
    """Multi-kill rounds per round (rounds where player got 2+ kills)."""
    mk = s.double_kills + s.triple_kills + s.quad_kills + s.penta_kills
    return mk / max(s.total_rounds, 1)

def _impact(kpr: float, mkpr: float) -> float:
    """
    Impact sub-rating.
    Formula: 2.13 * KPR + 2.63 * MKPR - 0.41
    MKPR = multi-kill rounds per total round.
    """
    return 2.13 * kpr + 2.63 * mkpr - 0.41

def _base_rating(kast_pct: float, kpr: float, dpr: float,
                  impact: float, adr: float) -> float:
    """
    Community reverse-engineered Rating 2.0 formula.
    Source: https://github.com/floxay/python-hltv (and community analysis)

    Rating = 0.0073*KAST + 0.3591*KPR - 0.5329*DPR + 0.2372*Impact + 0.0032*ADR + 0.1587
    """
    return (0.0073 * kast_pct
            + 0.3591 * kpr
            - 0.5329 * dpr
            + 0.2372 * impact
            + 0.0032 * adr
            + 0.1587)

def _sub_ratings(kpr: float, dpr: float, adr: float,
                  kast_pct: float, cal: dict) -> dict:
    """Normalised sub-rating components (1.00 = average)."""
    return {
        "kill":     round(kpr / cal["avg_kpr"], 3),
        "survival": round((1 - dpr) / (1 - cal["avg_dpr"]), 3),
        "damage":   round(adr / cal["avg_adr"], 3),
        "kast":     round((kast_pct / 100) / (cal["avg_kast"] / 100), 3),
    }

def _estimate_adr(s: PlayerMatchStats) -> float:
    """Fallback ADR estimate when not provided by API."""
    # Each kill ~82 dmg dealt on average, assists ~30 chip
    raw = (s.kills * 82 + s.assists * 30) / max(s.total_rounds, 1)
    return _clamp(raw, 0, 160)

def _estimate_kast(s: PlayerMatchStats) -> float:
    """
    Fallback KAST estimate when not provided by API.
    Rough model: KAST ≈ at least (K+A contribution) clipped to [45, 92].
    """
    contrib = (s.kills + s.assists * 0.5 + s.total_rounds * 0.5) / max(s.total_rounds, 1)
    return _clamp(contrib * 75, 45, 92)


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC CALCULATION FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

def calculate_rating_20(s: PlayerMatchStats) -> dict:
    """Rating 2.0 — CS:GO era calibration."""
    cal = _CAL_20
    adr = s.adr if s.adr > 0 else _estimate_adr(s)
    kast = s.kast if s.kast > 0 else _estimate_kast(s)

    kpr  = _kpr(s)
    dpr  = _dpr(s)
    mkpr = _mkpr(s)
    imp  = _impact(kpr, mkpr)
    r    = _clamp(_base_rating(kast, kpr, dpr, imp, adr))
    sub  = _sub_ratings(kpr, dpr, adr, kast, cal)

    return {
        "version":      "2.0",
        "rating":       round(r, 2),
        "kpr":          round(kpr, 3),
        "dpr":          round(dpr, 3),
        "adr":          round(adr, 1),
        "kast":         round(kast, 1),
        "impact":       round(imp, 3),
        "sub_ratings":  sub,
        "note":         "CS:GO era averages",
    }


def calculate_rating_21(s: PlayerMatchStats) -> dict:
    """
    Rating 2.1 — CS2 / MR12 calibration.
    Extra: passive-saver penalty (low KAST + suspiciously low DPR).
    """
    cal = _CAL_21
    adr = s.adr if s.adr > 0 else _estimate_adr(s)
    kast = s.kast if s.kast > 0 else _estimate_kast(s)

    kpr  = _kpr(s)
    dpr  = _dpr(s)
    mkpr = _mkpr(s)
    imp  = _impact(kpr, mkpr)

    # Saver penalty: HLTV 2.1 punishes saving in lost rounds.
    # Without round-outcome data we approximate: very low KAST + very low DPR
    # strongly suggests the player saved a lot.
    save_penalty = 0.0
    if kast < 60 and dpr < 0.22:
        save_penalty = 0.04
    elif kast < 65 and dpr < 0.25:
        save_penalty = 0.02

    r   = _clamp(_base_rating(kast, kpr, dpr, imp, adr) - save_penalty)
    sub = _sub_ratings(kpr, dpr, adr, kast, cal)

    return {
        "version":      "2.1",
        "rating":       round(r, 2),
        "kpr":          round(kpr, 3),
        "dpr":          round(dpr, 3),
        "adr":          round(adr, 1),
        "kast":         round(kast, 1),
        "impact":       round(imp, 3),
        "sub_ratings":  sub,
        "save_penalty": round(save_penalty, 3),
        "note":         "CS2 MR12 averages; save penalty applied if applicable",
    }


def calculate_rating_30_approx(s: PlayerMatchStats) -> dict:
    """
    Approximate Rating 3.0.

    Built on Rating 2.1 base, then two modifiers are applied:

    1. Eco-adjustment factor
       Real 3.0 weights kills by equipment tier (AK vs Glock, etc.).
       We proxy this with ADR-per-kill vs HS%: a player with very high
       HS% but low ADR likely padded with pistol/eco frags → mild penalty.
       Factor stays within [0.94, 1.06].

    2. Round Swing bonus
       Real Round Swing tracks win-probability delta per kill.
       We estimate it using:
         • Clutch wins (1v1, 1v2) — these happen behind in the round (high swing).
         • Triple/quad/penta kills — multi-kills in the same round are high-swing.
       Bonus is capped at ±0.12.

    This is NOT the real HLTV 3.0. It's a structural approximation.
    """
    base = calculate_rating_21(s)
    adr  = base["adr"]
    kpr  = base["kpr"]

    # --- Eco adjustment ---
    if s.kills > 0 and s.total_rounds > 0:
        adr_per_kill = adr / max(kpr, 0.01) / 100
        hs_ratio     = s.hs_pct / 100
        # High HS% relative to ADR suggests eco-padding → slight penalty
        # Low HS% with high ADR suggests clean rifle kills → slight bonus
        eco_factor = _clamp(1.0 + 0.06 * (1 - hs_ratio) * (1 - min(adr_per_kill, 1))
                                 - 0.03 * hs_ratio * (1 - min(adr_per_kill, 1)),
                            0.94, 1.06)
    else:
        eco_factor = 1.0

    # --- Round Swing estimate ---
    rounds = max(s.total_rounds, 1)
    # Clutch wins = winning from disadvantage = very high swing
    clutch_bonus = (s.clutch_1v1 * 0.025 + s.clutch_1v2 * 0.05) / rounds * rounds * 0.4
    # Multi-kill bonus — shared credit in real 3.0, so we halve
    mk_bonus = (s.triple_kills * 0.015 + s.quad_kills * 0.03 + s.penta_kills * 0.06) * 0.5
    swing_bonus = _clamp(clutch_bonus + mk_bonus, -0.08, 0.12)

    r30 = _clamp(base["rating"] * eco_factor + swing_bonus)

    return {
        "version":      "≈3.0",
        "rating":       round(r30, 2),
        "kpr":          base["kpr"],
        "dpr":          base["dpr"],
        "adr":          base["adr"],
        "kast":         base["kast"],
        "impact":       base["impact"],
        "sub_ratings":  base["sub_ratings"],
        "eco_factor":   round(eco_factor, 3),
        "swing_bonus":  round(swing_bonus, 3),
        "base_21":      base["rating"],
        "note":         "Approximate — Round Swing estimated from clutches/multi-kills.",
    }


# ──────────────────────────────────────────────────────────────────────────────
# UI HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def rating_color(r: float) -> int:
    """Discord embed hex color based on 2.1 rating."""
    if r >= 1.30: return 0x00C851   # Bright green — elite
    if r >= 1.15: return 0x4CAF50   # Green — great
    if r >= 1.05: return 0x8BC34A   # Light green — good
    if r >= 0.95: return 0xFFEB3B   # Yellow — average
    if r >= 0.85: return 0xFF9800   # Orange — below average
    return 0xF44336                  # Red — poor

def rating_label(r: float) -> str:
    if r >= 1.30: return "🟢 Elite"
    if r >= 1.15: return "🟢 Great"
    if r >= 1.05: return "🟡 Good"
    if r >= 0.95: return "🟡 Average"
    if r >= 0.85: return "🟠 Below Avg"
    return "🔴 Poor"

def bar(value: float, lo: float = 0.7, hi: float = 1.3, width: int = 8) -> str:
    """ASCII progress bar normalised between lo and hi."""
    frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    filled = round(frac * width)
    return "█" * filled + "░" * (width - filled)
