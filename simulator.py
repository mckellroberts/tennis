"""
Tennis Point-by-Point Match Simulator
=======================================
Simulates every point of a tennis match using historical serve statistics
from the SQLite database. Each point runs through a two-serve model:

  1. Does the first serve land?  (first_serve_pct)
  2a. YES → Ace?                 (ace_rate / first_serve_pct)
      NO  → Server wins rally?   (blended first_serve_win_pct)
  2b. NO  → Double fault?        (df_rate / (1 - first_serve_pct))
      NO  → Server wins rally?   (blended second_serve_win_pct)

At break points, the server's bp_save_pct nudges the probability up or down
relative to the tour average. A small rank-differential nudge is applied
throughout — large enough to matter across many points, small enough that
stats always dominate.

Also computes a theoretical win probability via Markov chain (fast closed-form)
alongside the simulated result so the frontend can show both.

Usage:
    python simulator.py
    python simulator.py --db tennis.db --player1 "Rafael Nadal" --player2 "Roger Federer" --surface Clay
    python simulator.py --list-players
"""

import sqlite3
import argparse
import sys
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Tennis Point-by-Point Match Simulator")
parser.add_argument("--db",           default="tennis.db")
parser.add_argument("--player1",      default=None)
parser.add_argument("--player2",      default=None)
parser.add_argument("--surface",      default=None, choices=["Hard", "Clay", "Grass", "Carpet"])
parser.add_argument("--best-of",      default=None, type=int, choices=[3, 5])
parser.add_argument("--list-players", action="store_true")
args, _ = parser.parse_known_args()

DB_PATH = args.db

# Tour is no longer needed for table selection — unified schema.
# Kept for CLI/API compatibility.
TOUR_VIEW = {
    "ATP": "player_surface_stats",
    "WTA": "player_surface_stats",
}

# Tour average BP save % — used to normalise the clutch adjustment
TOUR_AVG_BP_SAVE = 0.64

# Rank nudge: capped at ±3pp so stats always dominate
RANK_NUDGE_MAX   = 0.03
RANK_NUDGE_SCALE = 0.0003   # per ranking position difference

# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class PlayerStats:
    name:                 str
    player_id:            int
    surface:              str
    match_count:          int
    serve_win_pct:        float
    first_serve_pct:      float
    first_serve_win_pct:  float
    second_serve_win_pct: float
    ace_rate:             float
    df_rate:              float
    bp_save_pct:          float
    rank:                 Optional[float] = None


@dataclass
class PointOutcome:
    server_won:     bool
    ace:            bool = False
    double_fault:   bool = False
    first_serve_in: bool = False


@dataclass
class GameStats:
    server_won:        bool
    aces:              int = 0
    double_faults:     int = 0
    first_serves:      int = 0
    first_serves_in:   int = 0
    bp_faced:          int = 0
    bp_saved:          int = 0
    points_won:        int = 0
    points_lost:       int = 0


@dataclass
class SetResult:
    p1_won:         bool
    p1_games:       int
    p2_games:       int
    tiebreak:       Optional[tuple] = None   # (p1_pts, p2_pts) if played


@dataclass
class MatchAccumulator:
    """Running totals for both players across the whole match."""
    p1_aces:              int   = 0
    p2_aces:              int   = 0
    p1_dfs:               int   = 0
    p2_dfs:               int   = 0
    p1_first_serves:      int   = 0
    p2_first_serves:      int   = 0
    p1_first_serves_in:   int   = 0
    p2_first_serves_in:   int   = 0
    p1_bp_faced:          int   = 0   # break points p1 had to save
    p2_bp_faced:          int   = 0
    p1_bp_saved:          int   = 0
    p2_bp_saved:          int   = 0
    p1_bp_chances:        int   = 0   # break point opportunities p1 had
    p2_bp_chances:        int   = 0
    p1_bp_converted:      int   = 0
    p2_bp_converted:      int   = 0
    p1_points:            int   = 0
    p2_points:            int   = 0
    p1_games:             int   = 0
    p2_games:             int   = 0


# ── Database ──────────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"Could not open database '{DB_PATH}': {e}")
        sys.exit(1)


def fetch_simulation_context(
    conn:    sqlite3.Connection,
    p1_name: str,
    p2_name: str,
    surface: str,
    stats_view: str = "player_surface_stats",
) -> tuple[Optional["PlayerStats"], Optional["PlayerStats"], dict]:
    """
    Pull everything needed for both players in a single CTE chain:

      - Weighted surface stats (serve_win_pct, ace_rate, etc.)
      - Most recent rank (via Rankings view, which itself is a view over
        PlayerTournamentStats joined with Tournament)
      - Head-to-head record on this surface
      - Each player's overall win rate on this surface (actual W/L, not
        just serve stats — captures return game strength the serve stats
        don't fully reflect)

    The h2h and surface win rate are returned in the context dict so the
    caller can apply a small meta-nudge to the simulation and surface them
    in the result payload.
    """
    cur = conn.execute(f"""
        -- ── CTE 1: surface stats for both players ────────────────────────────
        WITH player_stats AS (
            SELECT player_id, player, surface, match_count,
                   serve_win_pct, first_serve_pct, first_serve_win_pct,
                   second_serve_win_pct, ace_rate, df_rate, bp_save_pct
            FROM {stats_view}
            WHERE player IN (?, ?) AND surface = ?
        ),

        -- ── CTE 2: most recent rank for each player ───────────────────────────
        -- Rankings is itself a view: Tournament JOIN PlayerTournamentStats JOIN Player
        -- We need the MAX(ranking_date) per player, then join back to get the rank.
        latest_rank AS (
            SELECT r.player_id, r.rank
            FROM Rankings r
            INNER JOIN (
                SELECT player_id, MAX(ranking_date) AS max_date
                FROM Rankings
                WHERE player_id IN (SELECT player_id FROM player_stats)
                GROUP BY player_id
            ) lr ON r.player_id    = lr.player_id
                AND r.ranking_date = lr.max_date
        ),

        -- ── CTE 3: head-to-head record on this surface ────────────────────────
        -- CROSS JOIN the two player IDs from player_stats so we don't need
        -- to hard-code them twice.
        player_ids AS (
            SELECT MAX(CASE WHEN player = ? THEN player_id END) AS p1_id,
                   MAX(CASE WHEN player = ? THEN player_id END) AS p2_id
            FROM player_stats
        ),
        h2h AS (
            SELECT
                SUM(CASE WHEN m.winner_id = pi.p1_id THEN 1 ELSE 0 END) AS p1_wins,
                SUM(CASE WHEN m.winner_id = pi.p2_id THEN 1 ELSE 0 END) AS p2_wins,
                COUNT(*) AS total
            FROM Match m
            JOIN Tournament t ON t.id = m.tournament_id
            CROSS JOIN player_ids pi
            WHERE t.surface = ?
              AND ((m.winner_id = pi.p1_id AND m.loser_id = pi.p2_id)
                OR (m.winner_id = pi.p2_id AND m.loser_id = pi.p1_id))
        ),

        -- ── CTE 4: actual surface W/L for each player ─────────────────────────
        -- Captures return-game strength that serve stats alone don't fully reflect.
        -- UNION ALL of winner-side and loser-side rows, then aggregate.
        surface_record AS (
            SELECT
                player_id,
                SUM(CASE WHEN result = 'W' THEN 1 ELSE 0 END)  AS wins,
                COUNT(*)                                          AS total
            FROM (
                SELECT winner_id AS player_id, 'W' AS result
                FROM Match m
                JOIN Tournament t ON t.id = m.tournament_id
                WHERE t.surface = ?
                  AND winner_id IN (SELECT player_id FROM player_stats)
                UNION ALL
                SELECT loser_id AS player_id, 'L' AS result
                FROM Match m
                JOIN Tournament t ON t.id = m.tournament_id
                WHERE t.surface = ?
                  AND loser_id IN (SELECT player_id FROM player_stats)
            )
            GROUP BY player_id
        )

        -- ── Final SELECT: join everything together ────────────────────────────
        SELECT
            ps.*,
            lr.rank,
            sr.wins              AS surface_wins,
            sr.total             AS surface_total,
            h2h.p1_wins,
            h2h.p2_wins,
            h2h.total            AS h2h_total
        FROM player_stats ps
        LEFT JOIN latest_rank    lr ON lr.player_id = ps.player_id
        LEFT JOIN surface_record sr ON sr.player_id = ps.player_id
        CROSS JOIN h2h
    """, (p1_name, p2_name, surface,   # player_stats WHERE
          p1_name, p2_name,             # player_ids CASE
          surface,                      # h2h WHERE surface
          surface, surface))            # surface_record WHERE

    rows = {r["player"]: r for r in cur.fetchall()}

    def _build(name: str) -> Optional[PlayerStats]:
        row = rows.get(name)
        if row is None:
            return None
        return PlayerStats(
            name                 = row["player"],
            player_id            = row["player_id"],
            surface              = surface,
            match_count          = row["match_count"],
            serve_win_pct        = float(row["serve_win_pct"]        or 0),
            first_serve_pct      = float(row["first_serve_pct"]      or 0.60),
            first_serve_win_pct  = float(row["first_serve_win_pct"]  or 0.70),
            second_serve_win_pct = float(row["second_serve_win_pct"] or 0.50),
            ace_rate             = float(row["ace_rate"]             or 0),
            df_rate              = float(row["df_rate"]              or 0),
            bp_save_pct          = float(row["bp_save_pct"]          or TOUR_AVG_BP_SAVE),
            rank                 = float(row["rank"]) if row["rank"] else None,
        )

    s1  = _build(p1_name)
    s2  = _build(p2_name)

    # H2H and surface context — used for meta-nudge and result payload
    any_row  = next(iter(rows.values()), None)
    context  = {
        "h2h_p1_wins":    int(any_row["p1_wins"])    if any_row else 0,
        "h2h_p2_wins":    int(any_row["p2_wins"])    if any_row else 0,
        "h2h_total":      int(any_row["h2h_total"])  if any_row else 0,
        "p1_surface_wins":  int(rows[p1_name]["surface_wins"])  if p1_name in rows else 0,
        "p1_surface_total": int(rows[p1_name]["surface_total"]) if p1_name in rows else 0,
        "p2_surface_wins":  int(rows[p2_name]["surface_wins"])  if p2_name in rows else 0,
        "p2_surface_total": int(rows[p2_name]["surface_total"]) if p2_name in rows else 0,
    }
    return s1, s2, context


# Thin wrapper kept for backward compatibility with server.py's /api/simulate
def fetch_player_stats(
    conn: sqlite3.Connection,
    player: str,
    surface: str,
    stats_view: str = "player_surface_stats",
) -> Optional[PlayerStats]:
    s, _, _ = fetch_simulation_context(conn, player, player, surface, stats_view)
    return s


def search_players(conn: sqlite3.Connection, query: str, stats_view: str) -> list[str]:
    cur = conn.execute(
        f"SELECT DISTINCT player FROM {stats_view}"
        " WHERE LOWER(player) LIKE LOWER(?) ORDER BY player",
        (f"%{query}%",),
    )
    return [r["player"] for r in cur.fetchall()]


def list_all_players(conn: sqlite3.Connection, stats_view: str):
    cur = conn.execute(
        f"SELECT player, surface, match_count,"
        f" ROUND(serve_win_pct*100,1) AS swp"
        f" FROM {stats_view} ORDER BY player, surface"
    )
    rows = cur.fetchall()
    print(f"\n{'Player':<30} {'Surface':<10} {'Matches':>8} {'Serve Win%':>11}")
    print("─" * 62)
    for r in rows:
        print(f"{r['player']:<30} {r['surface']:<10} {r['match_count']:>8} {r['swp']:>10.1f}%")


# ── Point probability helpers ─────────────────────────────────────────────────
def _blend(server_stat: float, returner_stat: float) -> float:
    """
    Blend a server's stat with the returner's resistance.
    A great returner lowers the server's effective win probability.
    """
    return (server_stat + (1.0 - returner_stat)) / 2.0


def _rank_nudge(server: PlayerStats, returner: PlayerStats) -> float:
    """
    Small probability nudge based on rank differential.
    Positive = server is ranked better (lower number) → slight boost.
    Capped at ±RANK_NUDGE_MAX so stats always dominate.
    """
    srv_r = server.rank   or 200.0
    ret_r = returner.rank or 200.0
    raw   = (ret_r - srv_r) * RANK_NUDGE_SCALE
    return max(-RANK_NUDGE_MAX, min(RANK_NUDGE_MAX, raw))


def _clutch_adjust(p: float, server: PlayerStats) -> float:
    """
    At break point, nudge the server's win probability based on how
    well they historically save break points relative to the tour average.
    Adjustment is intentionally small — about ±2.5pp at the extremes.
    """
    clutch = server.bp_save_pct / TOUR_AVG_BP_SAVE   # 1.0 = exactly average
    adjusted = p + (clutch - 1.0) * 0.05
    return max(0.0, min(1.0, adjusted))


# ── Point simulation ──────────────────────────────────────────────────────────
def play_point(
    server:          PlayerStats,
    returner:        PlayerStats,
    is_break_point:  bool = False,
) -> PointOutcome:
    """
    Simulate a single point. Returns a PointOutcome describing what happened.
    """
    nudge = _rank_nudge(server, returner)

    # ── First serve ───────────────────────────────────────────────────────────
    first_in = random.random() < server.first_serve_pct

    if first_in:
        # Ace on first serve?
        ace_prob = server.ace_rate / max(server.first_serve_pct, 1e-6)
        ace_prob = min(ace_prob, 0.50)          # sanity clamp
        if random.random() < ace_prob:
            return PointOutcome(server_won=True, ace=True, first_serve_in=True)

        p = _blend(server.first_serve_win_pct, returner.first_serve_win_pct) + nudge
        if is_break_point:
            p = _clutch_adjust(p, server)
        p = max(0.0, min(1.0, p))
        return PointOutcome(server_won=random.random() < p, first_serve_in=True)

    # ── Second serve ──────────────────────────────────────────────────────────
    fault_rate = max(1.0 - server.first_serve_pct, 1e-6)
    df_prob    = min(server.df_rate / fault_rate, 0.50)   # sanity clamp

    if random.random() < df_prob:
        return PointOutcome(server_won=False, double_fault=True)

    p = _blend(server.second_serve_win_pct, returner.second_serve_win_pct) + nudge
    if is_break_point:
        p = _clutch_adjust(p, server)
    p = max(0.0, min(1.0, p))
    return PointOutcome(server_won=random.random() < p)


# ── Game simulation ───────────────────────────────────────────────────────────
def _would_win_next(pts: int, other_pts: int) -> bool:
    """Return True if winning one more point from pts wins the game."""
    new = pts + 1
    return new >= 4 and new - other_pts >= 2


def play_game(server: PlayerStats, returner: PlayerStats) -> GameStats:
    """
    Simulate a full service game. Returns GameStats for the server's perspective.
    """
    srv_pts = 0
    ret_pts = 0
    stats   = GameStats(server_won=False)

    while True:
        is_bp = _would_win_next(ret_pts, srv_pts)
        if is_bp:
            stats.bp_faced += 1

        outcome = play_point(server, returner, is_break_point=is_bp)
        stats.first_serves    += 1
        stats.first_serves_in += int(outcome.first_serve_in)
        stats.aces            += int(outcome.ace)
        stats.double_faults   += int(outcome.double_fault)

        if outcome.server_won:
            srv_pts         += 1
            stats.points_won += 1
            if is_bp:
                stats.bp_saved += 1
        else:
            ret_pts          += 1
            stats.points_lost += 1

        # Win condition
        if srv_pts >= 4 and srv_pts - ret_pts >= 2:
            stats.server_won = True
            break
        if ret_pts >= 4 and ret_pts - srv_pts >= 2:
            stats.server_won = False
            break

    return stats


# ── Tiebreak simulation ───────────────────────────────────────────────────────
def play_tiebreak(p1: PlayerStats, p2: PlayerStats, p1_serving: bool) -> tuple[bool, tuple]:
    """
    Simulate a tiebreak. Server rotates: 1 point, then every 2 points.
    Returns (p1_won, (p1_pts, p2_pts)).
    """
    p1_pts   = 0
    p2_pts   = 0
    total    = 0            # total points played, drives serve rotation
    # p1_serving = who serves the first point

    while True:
        # Serve rotation: first point is served by p1_serving player,
        # then every 2 points the serve flips.
        # Point index 0: p1_serving serves
        # Points 1-2: other player
        # Points 3-4: p1_serving
        # ...
        if total == 0:
            server_is_p1 = p1_serving
        else:
            # After the first point, serve flips every 2
            block = (total - 1) // 2   # which 2-point block we're in
            server_is_p1 = p1_serving != (block % 2 == 0)

        server  = p1 if server_is_p1 else p2
        returner = p2 if server_is_p1 else p1

        # No clutch BP adjustment in tiebreaks (it's all high-pressure)
        outcome = play_point(server, returner, is_break_point=False)

        if (outcome.server_won and server_is_p1) or (not outcome.server_won and not server_is_p1):
            p1_pts += 1
        else:
            p2_pts += 1
        total += 1

        if p1_pts >= 7 and p1_pts - p2_pts >= 2:
            return True, (p1_pts, p2_pts)
        if p2_pts >= 7 and p2_pts - p1_pts >= 2:
            return False, (p1_pts, p2_pts)


# ── Set simulation ────────────────────────────────────────────────────────────
def play_set(
    p1: PlayerStats,
    p2: PlayerStats,
    p1_serving: bool,
    acc: MatchAccumulator,
) -> tuple[SetResult, bool]:
    """
    Simulate a full set.
    Returns (SetResult, p1_serving_next_set).
    acc is updated in place with running match totals.
    """
    p1_games    = 0
    p2_games    = 0
    p1_srv      = p1_serving

    while True:
        server  = p1 if p1_srv else p2
        returner = p2 if p1_srv else p1

        g = play_game(server, returner)

        # Accumulate stats into match totals
        if p1_srv:
            acc.p1_aces           += g.aces
            acc.p1_dfs            += g.double_faults
            acc.p1_first_serves   += g.first_serves
            acc.p1_first_serves_in += g.first_serves_in
            acc.p1_bp_faced       += g.bp_faced
            acc.p1_bp_saved       += g.bp_saved
            acc.p1_points         += g.points_won
            acc.p2_points         += g.points_lost
            # Break point opportunities for p2
            acc.p2_bp_chances     += g.bp_faced
            if not g.server_won:
                acc.p2_bp_converted += 1
        else:
            acc.p2_aces           += g.aces
            acc.p2_dfs            += g.double_faults
            acc.p2_first_serves   += g.first_serves
            acc.p2_first_serves_in += g.first_serves_in
            acc.p2_bp_faced       += g.bp_faced
            acc.p2_bp_saved       += g.bp_saved
            acc.p2_points         += g.points_won
            acc.p1_points         += g.points_lost
            # Break point opportunities for p1
            acc.p1_bp_chances     += g.bp_faced
            if not g.server_won:
                acc.p1_bp_converted += 1

        if g.server_won:
            if p1_srv:
                p1_games += 1
                acc.p1_games += 1
            else:
                p2_games += 1
                acc.p2_games += 1
        else:
            if p1_srv:
                p2_games += 1
                acc.p2_games += 1
            else:
                p1_games += 1
                acc.p1_games += 1

        # Flip serve for next game
        p1_srv = not p1_srv

        # ── Tiebreak at 6-6 ──────────────────────────────────────────────────
        if p1_games == 6 and p2_games == 6:
            # p1_srv is now the player who DIDN'T serve the last game,
            # which is correct — the player who received last serves first in TB
            p1_won_tb, tb_score = play_tiebreak(p1, p2, p1_srv)
            if p1_won_tb:
                p1_games += 1
                acc.p1_games += 1
            else:
                p2_games += 1
                acc.p2_games += 1
            # After tiebreak, serve goes to whoever receives first in next set
            # = the player who served first in this tiebreak's opponent
            p1_serving_next = not p1_srv
            return (
                SetResult(p1_won=p1_won_tb, p1_games=p1_games, p2_games=p2_games, tiebreak=tb_score),
                p1_serving_next,
            )

        # ── Normal set win ────────────────────────────────────────────────────
        if (p1_games >= 6 and p1_games - p2_games >= 2):
            return SetResult(p1_won=True,  p1_games=p1_games, p2_games=p2_games), p1_srv
        if (p2_games >= 6 and p2_games - p1_games >= 2):
            return SetResult(p1_won=False, p1_games=p1_games, p2_games=p2_games), p1_srv


# ── Markov chain — theoretical win probability ────────────────────────────────
@lru_cache(maxsize=None)
def _p_win_game(p: float) -> float:
    q = 1 - p
    no_deuce = p**4 + 4*(p**4)*q + 10*(p**4)*(q**2)
    p_deuce  = math.comb(6, 3) * (p**3) * (q**3)
    p_from_d = p**2 / (p**2 + q**2)
    return no_deuce + p_deuce * p_from_d


@lru_cache(maxsize=None)
def _p_win_set_exact(p_hold: float, p_break_opp: float) -> float:
    from functools import lru_cache as _lc

    @_lc(maxsize=None)
    def dp(i, j):
        if i == 6 and j == 6:
            denom = p_hold + p_break_opp
            return p_hold / denom if denom > 0 else 0.5
        if i >= 6 and i - j >= 2:
            return 1.0
        if j >= 6 and j - i >= 2:
            return 0.0
        if (i + j) % 2 == 0:
            p = p_hold
        else:
            p = p_break_opp
        return p * dp(i + 1, j) + (1 - p) * dp(i, j + 1)

    val = dp(0, 0)
    dp.cache_clear()
    return val


def _p_win_match(p_set: float, best_of: int) -> float:
    sets_needed = (best_of + 1) // 2
    q     = 1 - p_set
    total = 0.0
    for lost in range(sets_needed):
        ways   = math.comb(sets_needed - 1 + lost, lost)
        total += ways * (p_set ** sets_needed) * (q ** lost)
    return total


def _theoretical_prob(s1: PlayerStats, s2: PlayerStats, best_of: int) -> dict:
    """Return the Markov-chain theoretical win probabilities."""
    eff_p1 = max(0.35, min(0.75, (s1.serve_win_pct + (1.0 - s2.serve_win_pct)) / 2.0))
    eff_p2 = max(0.35, min(0.75, (s2.serve_win_pct + (1.0 - s1.serve_win_pct)) / 2.0))

    pg1 = _p_win_game(eff_p1)
    pg2 = _p_win_game(eff_p2)

    ps1_a = _p_win_set_exact(pg1, 1.0 - pg2)
    ps1_b = 1.0 - _p_win_set_exact(pg2, 1.0 - pg1)
    ps1   = (ps1_a + ps1_b) / 2.0
    pm1   = _p_win_match(ps1, best_of)

    return {
        "p1_eff_serve_win": eff_p1,
        "p2_eff_serve_win": eff_p2,
        "p1_hold_pct":      pg1,
        "p2_hold_pct":      pg2,
        "p1_set_pct":       ps1,
        "p1_match_pct":     pm1,
    }


# ── Full match simulation ─────────────────────────────────────────────────────
def simulate(
    p1_name: str,
    p2_name: str,
    surface: str,
    best_of: int,
    conn: sqlite3.Connection,
    stats_view: str = "player_surface_stats",
) -> dict:
    """
    Simulate one full match point by point.
    Returns a result dict compatible with server.py's /api/simulate endpoint.
    """
    s1, s2, ctx = fetch_simulation_context(conn, p1_name, p2_name, surface, stats_view)

    if s1 is None:
        return {"error": f"No {surface} stats found for '{p1_name}'"}
    if s2 is None:
        return {"error": f"No {surface} stats found for '{p2_name}'"}

    # ── Meta-nudge from h2h and surface win rate ──────────────────────────────
    # These are small adjustments that nudge the effective serve win probability
    # when we have strong historical evidence beyond the serve stats alone.
    #
    # H2H: if one player consistently wins their matchup, that tendency persists.
    # Capped at ±1.5pp so it only tips truly close calls.
    h2h_nudge = 0.0
    if ctx["h2h_total"] >= 5:
        h2h_rate   = ctx["h2h_p1_wins"] / ctx["h2h_total"]
        h2h_nudge  = (h2h_rate - 0.5) * 0.03   # max ±1.5pp

    # Surface win rate vs serve_win_pct gap: a player who wins more matches
    # than their serve stats predict is doing it through their return game.
    # A fraction of that gap is applied as a bonus to their effective serve pct.
    def _surface_return_bonus(wins, total, serve_win_pct):
        if total < 20:
            return 0.0
        actual_win_rate = wins / total
        gap = actual_win_rate - serve_win_pct   # positive = better than serve stats suggest
        return max(-0.02, min(0.02, gap * 0.15))

    s1_bonus = _surface_return_bonus(ctx["p1_surface_wins"], ctx["p1_surface_total"], s1.serve_win_pct)
    s2_bonus = _surface_return_bonus(ctx["p2_surface_wins"], ctx["p2_surface_total"], s2.serve_win_pct)

    # Patch the effective serve_win_pct so downstream Markov and point logic
    # both see the adjusted value — we don't mutate the dataclass, just shadow it.
    import dataclasses
    s1 = dataclasses.replace(s1, serve_win_pct=min(0.80, s1.serve_win_pct + h2h_nudge  + s1_bonus))
    s2 = dataclasses.replace(s2, serve_win_pct=min(0.80, s2.serve_win_pct - h2h_nudge  + s2_bonus))

    sets_needed = (best_of + 1) // 2
    acc         = MatchAccumulator()
    sets_played = []
    p1_sets     = 0
    p2_sets     = 0

    # Randomly decide who serves first
    p1_serving  = random.random() < 0.5

    while p1_sets < sets_needed and p2_sets < sets_needed:
        set_result, p1_serving = play_set(s1, s2, p1_serving, acc)
        sets_played.append(set_result)
        if set_result.p1_won:
            p1_sets += 1
        else:
            p2_sets += 1

    winner = p1_name if p1_sets > p2_sets else p2_name

    # Human-readable score string e.g. "6-4 3-6 7-6(4)"
    score_parts = []
    for s in sets_played:
        part = f"{s.p1_games}-{s.p2_games}"
        if s.tiebreak:
            losing_tb = min(s.tiebreak)
            part += f"({losing_tb})"
        score_parts.append(part)
    score_str = " ".join(score_parts)

    # Actual first serve % from this match
    p1_fsp = (acc.p1_first_serves_in / acc.p1_first_serves) if acc.p1_first_serves else 0
    p2_fsp = (acc.p2_first_serves_in / acc.p2_first_serves) if acc.p2_first_serves else 0

    theory = _theoretical_prob(s1, s2, best_of)

    return {
        # ── Match summary ────────────────────────────────────────────────────
        "player1":      p1_name,
        "player2":      p2_name,
        "winner":       winner,
        "surface":      surface,
        "best_of":      best_of,
        "score":        score_str,

        # ── Set breakdown ────────────────────────────────────────────────────
        "sets": [
            {
                "p1":       s.p1_games,
                "p2":       s.p2_games,
                "tiebreak": list(s.tiebreak) if s.tiebreak else None,
            }
            for s in sets_played
        ],

        # ── In-match stats (from the simulation) ─────────────────────────────
        "p1_aces":         acc.p1_aces,
        "p2_aces":         acc.p2_aces,
        "p1_dfs":          acc.p1_dfs,
        "p2_dfs":          acc.p2_dfs,
        "p1_first_in_pct": p1_fsp,
        "p2_first_in_pct": p2_fsp,
        "p1_bp_chances":   acc.p1_bp_chances,   # break point opportunities
        "p2_bp_chances":   acc.p2_bp_chances,
        "p1_bp_converted": acc.p1_bp_converted,
        "p2_bp_converted": acc.p2_bp_converted,
        "p1_bp_faced":     acc.p1_bp_faced,     # break points they had to save
        "p2_bp_faced":     acc.p2_bp_faced,
        "p1_bp_saved":     acc.p1_bp_saved,
        "p2_bp_saved":     acc.p2_bp_saved,
        "p1_points":       acc.p1_points,
        "p2_points":       acc.p2_points,
        "p1_games":        acc.p1_games,
        "p2_games":        acc.p2_games,

        # ── Historical weighted stats (inputs) ───────────────────────────────
        "p1_matches":       s1.match_count,
        "p2_matches":       s2.match_count,
        "p1_raw_serve_win": s1.serve_win_pct,
        "p2_raw_serve_win": s2.serve_win_pct,
        "p1_first_serve":   s1.first_serve_pct,
        "p2_first_serve":   s2.first_serve_pct,
        "p1_ace_rate":      s1.ace_rate,
        "p2_ace_rate":      s2.ace_rate,
        "p1_bp_save":       s1.bp_save_pct,
        "p2_bp_save":       s2.bp_save_pct,

        # ── Theoretical win probability (Markov chain) ───────────────────────
        **theory,

        # ── Head-to-head and surface context (from the CTE query) ─────────────
        "h2h_p1_wins":      ctx["h2h_p1_wins"],
        "h2h_p2_wins":      ctx["h2h_p2_wins"],
        "h2h_total":        ctx["h2h_total"],
        "p1_surface_wins":  ctx["p1_surface_wins"],
        "p1_surface_total": ctx["p1_surface_total"],
        "p2_surface_wins":  ctx["p2_surface_wins"],
        "p2_surface_total": ctx["p2_surface_total"],
        "p1_surface_win_pct": (
            ctx["p1_surface_wins"] / ctx["p1_surface_total"]
            if ctx["p1_surface_total"] else None
        ),
        "p2_surface_win_pct": (
            ctx["p2_surface_wins"] / ctx["p2_surface_total"]
            if ctx["p2_surface_total"] else None
        ),
    }


# ── CLI display ───────────────────────────────────────────────────────────────
SURFACE_EMOJI = {"Hard": "🔵", "Clay": "🟠", "Grass": "🟢", "Carpet": "⬛"}
SCORE_LABELS  = ["0", "15", "30", "40"]


def print_result(r: dict):
    p1  = r["player1"]
    p2  = r["player2"]
    pm1 = r["p1_match_pct"]
    pm2 = 1 - pm1

    print()
    print("═" * 64)
    print(f"  {SURFACE_EMOJI.get(r['surface'],'')}  {r['surface'].upper()} — Best of {r['best_of']}")
    print("═" * 64)

    # Winner banner
    print(f"\n  🏆  {r['winner'].upper()} wins  {r['score']}\n")

    # Theoretical probability bar
    bar_w = 48
    fill  = round(pm1 * bar_w)
    print(f"  Theoretical win probability")
    print(f"  {p1:<22}  {p2}")
    print(f"  \033[36m{'█'*fill}\033[0m\033[90m{'░'*(bar_w-fill)}\033[0m")
    print(f"  \033[36m{pm1*100:>5.1f}%\033[0m{'':<36}\033[33m{pm2*100:.1f}%\033[0m")
    print()

    # Set scores
    print(f"  {'SET SCORES':}")
    for i, s in enumerate(r["sets"], 1):
        tb = f"  (TB {s['tiebreak'][0]}-{s['tiebreak'][1]})" if s["tiebreak"] else ""
        print(f"    Set {i}: {s['p1']}-{s['p2']}{tb}")
    print()

    # Stats table
    def row(label, v1, v2, fmt="", pct=False):
        s  = "%" if pct else ""
        m  = 100  if pct else 1
        v1s = f"{v1*m:{fmt}}{s}" if isinstance(v1, float) else str(v1)
        v2s = f"{v2*m:{fmt}}{s}" if isinstance(v2, float) else str(v2)
        print(f"  {label:<28} {v1s:>8}   {v2s:>8}")

    p1s = p1[:10]
    p2s = p2[:10]
    print(f"  {'STAT':<28} {p1s:>8}   {p2s:>8}")
    print(f"  {'─'*28} {'─'*8}   {'─'*8}")
    row("Aces",              r["p1_aces"],         r["p2_aces"])
    row("Double faults",     r["p1_dfs"],          r["p2_dfs"])
    row("1st serve % (match)",r["p1_first_in_pct"], r["p2_first_in_pct"], fmt=".1f", pct=True)
    row("1st serve % (hist)", r["p1_first_serve"],  r["p2_first_serve"],  fmt=".1f", pct=True)
    row("Serve win % (hist)", r["p1_raw_serve_win"],r["p2_raw_serve_win"],fmt=".1f", pct=True)
    row("BP chances",        r["p1_bp_chances"],   r["p2_bp_chances"])
    row("BP converted",      r["p1_bp_converted"], r["p2_bp_converted"])
    row("BP saved",          r["p1_bp_saved"],     r["p2_bp_saved"])
    row("Points won",        r["p1_points"],       r["p2_points"])
    row("Games won",         r["p1_games"],        r["p2_games"])
    print()

    margin = abs(pm1 - pm2)
    fav    = p1 if pm1 > pm2 else p2
    if margin < 0.05:
        verdict = "Coin flip — too close to call"
    elif margin < 0.15:
        verdict = f"{fav} has a slight edge"
    elif margin < 0.30:
        verdict = f"{fav} is the clear favourite"
    else:
        verdict = f"{fav} is heavily favoured"
    print(f"  📊 {verdict}")
    print("═" * 64)
    print()


# ── Interactive mode ──────────────────────────────────────────────────────────
def _pick_player(conn, prompt: str, stats_view: str) -> str:
    while True:
        query   = input(prompt).strip()
        matches = search_players(conn, query, stats_view)
        if not matches:
            print(f"  No players found matching '{query}'.")
            continue
        if len(matches) == 1:
            print(f"  → {matches[0]}")
            return matches[0]
        for i, m in enumerate(matches[:15], 1):
            print(f"    {i:>2}. {m}")
        while True:
            ch = input("  Pick number (0 to search again): ").strip()
            if ch == "0":
                break
            try:
                idx = int(ch) - 1
                if 0 <= idx < len(matches[:15]):
                    return matches[idx]
            except ValueError:
                pass


def interactive(conn: sqlite3.Connection):
    print()
    print("╔══════════════════════════════════════════╗")
    print("║  🎾  Tennis Point-by-Point Simulator     ║")
    print("╚══════════════════════════════════════════╝")
    stats_view = "player_surface_stats"

    while True:
        p1      = _pick_player(conn, "\nPlayer 1: ", stats_view)
        p2      = _pick_player(conn, "Player 2: ", stats_view)
        surfaces = ["Hard", "Clay", "Grass", "Carpet"]
        for i, s in enumerate(surfaces, 1):
            print(f"  {i}. {SURFACE_EMOJI.get(s,'')} {s}")
        while True:
            ch = input("  Surface (1-4): ").strip()
            try:
                surface = surfaces[int(ch) - 1]
                break
            except (ValueError, IndexError):
                pass
        bo_in   = input("  Best of 3 or 5? [3]: ").strip()
        best_of = 5 if bo_in == "5" else 3

        result = simulate(p1, p2, surface, best_of, conn)
        if "error" in result:
            print(f"\n  ⚠  {result['error']}")
        else:
            print_result(result)

        if input("Another simulation? [Y/n]: ").strip().lower() == "n":
            break

    print("\nGood match! 🎾\n")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    conn       = get_conn()
    stats_view = "player_surface_stats"

    if args.list_players:
        list_all_players(conn, stats_view)
        conn.close()
        return

    if args.player1 and args.player2 and args.surface:
        best_of = args.best_of or 3
        result  = simulate(args.player1, args.player2, args.surface, best_of, conn)
        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)
        print_result(result)
    else:
        interactive(conn)

    conn.close()


if __name__ == "__main__":
    main()