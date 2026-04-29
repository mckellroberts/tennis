"""
Tennis What-If Match Simulator
================================
Given two player names and a surface, uses historical serve statistics from
the SQLite database to estimate each player's probability of winning via a
Markov-chain tennis model. Supports both ATP (men) and WTA (women) tours.

Usage:
    python simulator.py                         # interactive mode
    python simulator.py --db atp.db             # specify db path
    python simulator.py --tour WTA --player1 "Iga Swiatek" --player2 "Aryna Sabalenka" --surface Clay
    python simulator.py --list-players          # show all players with stats
    python simulator.py --tour WTA --list-players

How the math works:
    1. Pull each player's average serve_win_pct (p) per surface from SQLite.
    2. Blend opponent's return strength: p_eff = (p_serve + (1 - opp_serve)) / 2
    3. Markov chain: P(win game) → P(win set) → P(win match)
"""

import sqlite3
import argparse
import sys
import math
from functools import lru_cache
from typing import Optional

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Tennis What-If Match Simulator")
parser.add_argument("--db",      default="atp.db", help="SQLite database path")
parser.add_argument("--tour",    default=None, choices=["ATP","WTA"],
                    help="Tour to simulate (ATP=men, WTA=women)")
parser.add_argument("--player1", default=None)
parser.add_argument("--player2", default=None)
parser.add_argument("--surface", default=None, choices=["Hard","Clay","Grass","Carpet"])
parser.add_argument("--best-of", default=None, type=int, choices=[3,5],
                    help="Best of 3 or 5 sets (default: 3 for WTA, 3 for ATP non-Slams)")
parser.add_argument("--list-players", action="store_true",
                    help="Print all players that have surface stats in the DB")
args, _ = parser.parse_known_args()

DB_PATH = args.db

TOUR_VIEW = {
    "ATP": "player_surface_stats",
    "WTA": "wta_player_surface_stats",
}

# ── Database helpers ──────────────────────────────────────────────────────────
def get_conn():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"Could not open database '{DB_PATH}': {e}")
        print("Run setup_db.py first.")
        sys.exit(1)

def fetch_player_stats(conn, player: str, surface: str, stats_view: str) -> Optional[sqlite3.Row]:
    """Return player stats row for given surface, or None if not found."""
    cur = conn.execute(
        f"SELECT * FROM {stats_view} WHERE player = ? AND surface = ?",
        (player, surface)
    )
    return cur.fetchone()

def search_players(conn, query: str, stats_view: str) -> list[str]:
    """Fuzzy search player names (case-insensitive substring)."""
    cur = conn.execute(
        f"SELECT DISTINCT player FROM {stats_view}"
        " WHERE LOWER(player) LIKE LOWER(?) ORDER BY player",
        (f"%{query}%",)
    )
    return [r["player"] for r in cur.fetchall()]

def list_all_players(conn, stats_view: str):
    cur = conn.execute(
        f"SELECT player, surface, match_count, ROUND(serve_win_pct*100,1) AS swp"
        f" FROM {stats_view} ORDER BY player, surface"
    )
    rows = cur.fetchall()
    print(f"\n{'Player':<30} {'Surface':<10} {'Matches':>8} {'Serve Win%':>11}")
    print("─" * 62)
    for r in rows:
        print(f"{r['player']:<30} {r['surface']:<10} {r['match_count']:>8} {r['swp']:>10.1f}%")

# ── Markov chain math ─────────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def p_win_game(p: float) -> float:
    """
    Probability server wins a game given p = P(server wins a point).
    Uses the exact closed-form solution for deuce games.
    """
    q = 1 - p
    # P(reach deuce) after 3-3: C(6,3) * (p*q)^3... handled by deuce formula
    # P(win without deuce): sum of paths where server leads 4-0, 4-1, 4-2
    no_deuce = (
        p**4 +
        4 * p**4 * q +
        10 * p**4 * q**2
    )
    # P(reach 3-3 = deuce) × P(win from deuce)
    p_deuce = math.comb(6, 3) * (p**3) * (q**3)
    p_win_from_deuce = p**2 / (p**2 + q**2)
    return no_deuce + p_deuce * p_win_from_deuce

@lru_cache(maxsize=None)
def p_win_tiebreak(p: float) -> float:
    """
    P(server of first point wins tiebreak) given p = P(win a point on own serve).
    Approximation: alternating serve, each player serves 2 points in a row (after first).
    We treat it as each player wins ~50% of serve points in a tiebreak and adjust slightly.
    Simple iid approximation: treat as p throughout.
    """
    # Tiebreak: first to 7 with 2-clear. Same deuce formula applied at 6-6.
    q = 1 - p
    total = 0.0
    # Win 7-0 through 7-5
    for lost in range(6):
        ways = math.comb(6 + lost, lost) * math.comb(1, 0)  # server wins last point
        # Actually: win 7 before opponent wins 7, no tiebreak-within-tiebreak
        # P(reach score where server has 7, opponent has `lost`)
        # = C(6+lost, lost) * p^6 * q^lost   [opponent wins `lost`, then server wins last]
        total += math.comb(6 + lost, lost) * (p ** 7) * (q ** lost)
    # Deuce at 6-6
    p_reach_66 = math.comb(12, 6) * (p**6) * (q**6)
    p_win_from_66 = p**2 / (p**2 + q**2)
    return total + p_reach_66 * p_win_from_66

@lru_cache(maxsize=None)
def p_win_set(pg_server: float, pg_returner: float) -> float:
    """
    P(server of the set wins it) given:
        pg_server   = P(server wins their service game)
        pg_returner = P(returner wins their service game)  [≈ 1 - pg_opponent_on_serve]

    Uses exact recursion up to 6-6 then tiebreak.
    """
    # pg_returner here is P(returner holds serve) —
    # from set-server's perspective, P(break) = 1 - pg_returner
    p_hold   = pg_server      # server holds
    p_break  = 1 - pg_returner  # server breaks (returner fails to hold)

    # dp[i][j] = P(set server leads i games to j)
    # Build iteratively
    dp = [[0.0] * 14 for _ in range(14)]
    dp[0][0] = 1.0

    result = 0.0

    for i in range(13):
        for j in range(13):
            if dp[i][j] == 0:
                continue
            prob = dp[i][j]
            total_games = i + j

            # Tiebreak at 6-6
            if i == 6 and j == 6:
                # Approximate: use average serve win on points for tiebreak
                # We'll pass in point-level p later; for now use game-level approx
                p_tb = pg_server / (pg_server + (1 - pg_returner))
                result += prob * p_tb
                continue

            # Determine whose serve it is (alternates each game)
            if total_games % 2 == 0:
                p_win_game_now = p_hold    # set-server serving
            else:
                p_win_game_now = p_break   # set-server returning

            new_i = i + 1
            new_j = j + 1

            # Server wins game → i+1
            if new_i <= 7 or (new_i == 7 and new_j <= 5):
                # Check win condition
                if new_i >= 6 and new_i - j >= 2:
                    result += prob * p_win_game_now
                else:
                    dp[new_i][j] += prob * p_win_game_now

            # Server loses game → j+1
            if new_j >= 6 and new_j - i >= 2:
                pass  # opponent wins set, doesn't contribute to result
            else:
                dp[i][new_j] += prob * (1 - p_win_game_now)

    # Re-implement cleanly with proper state machine
    return _p_win_set_exact(pg_server, pg_returner)

def _p_win_set_exact(p_hold: float, p_break_opp: float) -> float:
    """
    Exact DP for P(set server wins set).
    p_hold      = P(server holds own serve)
    p_break_opp = P(server breaks opponent) = 1 - P(opponent holds)
    """
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def dp(i, j):
        """P(set server wins from score i-j)"""
        if i == 6 and j == 6:
            # Tiebreak: approximate with ratio of hold rates
            denom = p_hold + p_break_opp
            return p_hold / denom if denom > 0 else 0.5
        if i >= 6 and i - j >= 2:
            return 1.0
        if j >= 6 and j - i >= 2:
            return 0.0

        total_games = i + j
        if total_games % 2 == 0:  # set-server's serve
            p = p_hold
        else:                      # opponent's serve
            p = p_break_opp

        return p * dp(i + 1, j) + (1 - p) * dp(i, j + 1)

    val = dp(0, 0)
    dp.cache_clear()
    return val

def p_win_match(p_set: float, best_of: int) -> float:
    """
    P(player wins match) given p_set = P(they win any given set).
    Best of 3: need 2 sets. Best of 5: need 3 sets.
    """
    sets_needed = (best_of + 1) // 2
    q = 1 - p_set
    total = 0.0
    for lost in range(sets_needed):
        # Win `sets_needed` sets, lose exactly `lost` sets
        # Last set must be a win → C(sets_needed-1+lost, lost) ways for prior sets
        ways = math.comb(sets_needed - 1 + lost, lost)
        total += ways * (p_set ** sets_needed) * (q ** lost)
    return total

# ── Core simulation ───────────────────────────────────────────────────────────
def simulate(
    p1_name: str,
    p2_name: str,
    surface: str,
    best_of: int,
    conn: sqlite3.Connection,
    stats_view: str = "player_surface_stats",
) -> dict:

    s1 = fetch_player_stats(conn, p1_name, surface, stats_view)
    s2 = fetch_player_stats(conn, p2_name, surface, stats_view)

    if s1 is None:
        return {"error": f"No {surface} stats found for '{p1_name}'"}
    if s2 is None:
        return {"error": f"No {surface} stats found for '{p2_name}'"}

    # Raw serve win probabilities (point level)
    raw_p1 = s1["serve_win_pct"]
    raw_p2 = s2["serve_win_pct"]

    # Blend: effective serve win pct accounts for opponent's return quality
    # Return quality ≈ 1 - opponent's serve_win_pct (implied return dominance)
    eff_p1 = (raw_p1 + (1.0 - raw_p2)) / 2.0
    eff_p2 = (raw_p2 + (1.0 - raw_p1)) / 2.0

    # Clamp to sane range
    eff_p1 = max(0.35, min(0.75, eff_p1))
    eff_p2 = max(0.35, min(0.75, eff_p2))

    # Game probabilities
    pg1_hold  = p_win_game(eff_p1)  # P1 holds serve
    pg2_hold  = p_win_game(eff_p2)  # P2 holds serve

    # Set probabilities (from P1's perspective as set-server first)
    # In a set, server alternates. We need:
    #   P(P1 holds) and P(P1 breaks P2)
    p_break_p2 = 1.0 - pg2_hold

    ps1 = _p_win_set_exact(pg1_hold, p_break_p2)  # P1 wins set when serving first
    # Average over who serves first (roughly 50/50 over many matches)
    # When P2 serves first: P1 is returner → invert
    ps1_p2_serves_first = 1.0 - _p_win_set_exact(pg2_hold, 1.0 - pg1_hold)
    ps1_avg = (ps1 + ps1_p2_serves_first) / 2.0

    # Match probability
    pm1 = p_win_match(ps1_avg, best_of)

    return {
        "player1": p1_name,
        "player2": p2_name,
        "surface": surface,
        "best_of": best_of,
        "p1_matches": s1["match_count"],
        "p2_matches": s2["match_count"],
        "p1_raw_serve_win": raw_p1,
        "p2_raw_serve_win": raw_p2,
        "p1_eff_serve_win": eff_p1,
        "p2_eff_serve_win": eff_p2,
        "p1_hold_pct":     pg1_hold,
        "p2_hold_pct":     pg2_hold,
        "p1_set_pct":      ps1_avg,
        "p1_match_pct":    pm1,
        "p1_first_serve":  s1["first_serve_pct"],
        "p2_first_serve":  s2["first_serve_pct"],
        "p1_ace_rate":     s1["ace_rate"],
        "p2_ace_rate":     s2["ace_rate"],
        "p1_bp_save":      s1["bp_save_pct"],
        "p2_bp_save":      s2["bp_save_pct"],
    }

# ── Display ───────────────────────────────────────────────────────────────────
SURFACE_EMOJI = {"Hard": "🔵", "Clay": "🟠", "Grass": "🟢", "Carpet": "⬛"}

def bar(pct: float, width: int = 40) -> str:
    filled = round(pct * width)
    empty  = width - filled
    return "█" * filled + "░" * empty

def print_result(r: dict):
    p1 = r["player1"]
    p2 = r["player2"]
    pm1 = r["p1_match_pct"]
    pm2 = 1 - pm1
    emoji = SURFACE_EMOJI.get(r["surface"], "")

    print()
    print("═" * 60)
    print(f"  {emoji}  {r['surface'].upper()} — Best of {r['best_of']}")
    print("═" * 60)
    print()

    # Win probability bar
    print(f"  {'WIN PROBABILITY':}")
    print(f"  {p1:<22}  {p2}")
    p1_bar = bar(pm1, 44)
    split = round(pm1 * 44)
    print(f"  \033[36m{'█'*split}\033[0m\033[90m{'░'*(44-split)}\033[0m")
    print(f"  \033[36m{pm1*100:>5.1f}%\033[0m {'':>32} \033[33m{pm2*100:.1f}%\033[0m")
    print()

    # Stat table
    def row(label, v1, v2, fmt=".1f", pct=True):
        s = "%" if pct else ""
        mul = 100 if pct else 1
        v1s = f"{v1*mul:{fmt}}{s}" if v1 is not None else "N/A"
        v2s = f"{v2*mul:{fmt}}{s}" if v2 is not None else "N/A"
        print(f"  {label:<24} {v1s:>8}   {v2s:>8}")

    print(f"  {'STAT':<24} {p1[:8+1]:>9}   {p2[:8+1]:>9}")
    print(f"  {'─'*24} {'─'*9}   {'─'*9}")
    row("Historical matches",  r["p1_matches"],     r["p2_matches"],     fmt=".0f", pct=False)
    row("Raw serve win %",     r["p1_raw_serve_win"], r["p2_raw_serve_win"])
    row("Eff. serve win %",    r["p1_eff_serve_win"], r["p2_eff_serve_win"])
    row("Hold % (game)",       r["p1_hold_pct"],    r["p2_hold_pct"])
    row("Set win %",           r["p1_set_pct"],     1 - r["p1_set_pct"])
    row("1st serve %",         r["p1_first_serve"], r["p2_first_serve"])
    row("Ace rate",            r["p1_ace_rate"],    r["p2_ace_rate"])
    row("BP save %",           r["p1_bp_save"],     r["p2_bp_save"])
    print()

    winner = p1 if pm1 > pm2 else p2
    margin = abs(pm1 - pm2)
    if margin < 0.05:
        verdict = "Coin flip — too close to call"
    elif margin < 0.15:
        verdict = f"{winner} has a slight edge"
    elif margin < 0.30:
        verdict = f"{winner} is the clear favorite"
    else:
        verdict = f"{winner} is heavily favored"
    print(f"  📊 {verdict}")
    print("═" * 60)
    print()

# ── Interactive mode ──────────────────────────────────────────────────────────
def pick_tour() -> str:
    print("\n  Tour:")
    print("    1. ATP (Men)")
    print("    2. WTA (Women)")
    while True:
        choice = input("  Pick tour (1-2): ").strip()
        if choice == "1":
            return "ATP"
        if choice == "2":
            return "WTA"

def pick_player(conn, prompt: str, stats_view: str) -> str:
    while True:
        query = input(prompt).strip()
        if not query:
            continue
        matches = search_players(conn, query, stats_view)
        if not matches:
            print(f"  No players found matching '{query}'. Try a shorter name.")
            continue
        if len(matches) == 1:
            print(f"  → Found: {matches[0]}")
            return matches[0]
        print(f"  Multiple matches:")
        for i, m in enumerate(matches[:15], 1):
            print(f"    {i:>2}. {m}")
        while True:
            choice = input("  Pick number (or 0 to search again): ").strip()
            if choice == "0":
                break
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(matches[:15]):
                    return matches[idx]
            except ValueError:
                pass

def pick_surface(conn, player1: str, player2: str) -> str:
    surfaces = ["Hard", "Clay", "Grass", "Carpet"]
    print("\n  Surfaces:")
    for i, s in enumerate(surfaces, 1):
        print(f"    {i}. {SURFACE_EMOJI.get(s,'')} {s}")
    while True:
        choice = input("  Pick surface (1-4): ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(surfaces):
                return surfaces[idx]
        except ValueError:
            pass

def interactive(conn, tour: Optional[str] = None):
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   🎾  Tennis What-If Match Simulator     ║")
    print("║   Powered by historical serve stats      ║")
    print("╚══════════════════════════════════════════╝")
    print()

    while True:
        current_tour = tour or pick_tour()
        stats_view = TOUR_VIEW[current_tour]
        default_bo = 3  # WTA is always best-of-3; ATP Slams can be 5

        p1 = pick_player(conn, "\nPlayer 1 name (partial OK): ", stats_view)
        p2 = pick_player(conn, "Player 2 name (partial OK): ", stats_view)
        surface = pick_surface(conn, p1, p2)

        if current_tour == "ATP":
            bo_input = input("Best of 3 or 5? [3]: ").strip()
            best_of = 5 if bo_input == "5" else 3
        else:
            best_of = 3

        result = simulate(p1, p2, surface, best_of, conn, stats_view)
        if "error" in result:
            print(f"\n  ⚠  {result['error']}")
        else:
            print_result(result)

        again = input("Run another simulation? [Y/n]: ").strip().lower()
        if again == "n":
            break

    print("\nGood match! 🎾\n")

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    conn = get_conn()
    tour = args.tour or "ATP"
    stats_view = TOUR_VIEW[tour]

    if args.list_players:
        list_all_players(conn, stats_view)
        return

    if args.player1 and args.player2 and args.surface:
        best_of = args.best_of or 3
        result = simulate(args.player1, args.player2, args.surface, best_of, conn, stats_view)
        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)
        print_result(result)
    else:
        interactive(conn, tour=args.tour)

    conn.close()

if __name__ == "__main__":
    main()