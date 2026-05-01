"""
Court Analytics — Flask API server
====================================
Serves the frontend and provides API endpoints backed by the SQLite DB.

Usage:
    python server.py            # http://localhost:5000
    python server.py --port 8080
    python server.py --db atp.db
"""

import os
import argparse
import sqlite3

from flask import Flask, jsonify, request, send_from_directory, abort

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Court Analytics server")
parser.add_argument("--db",   default="atp.db")
parser.add_argument("--port", default=5000, type=int)
args, _ = parser.parse_known_args()

BASE         = os.path.dirname(__file__)
DB_PATH      = os.path.join(BASE, args.db)
FRONTEND_DIR = os.path.join(BASE, "frontend")

TOUR_VIEW = {
    "ATP": "player_surface_stats",
    "WTA": "wta_player_surface_stats",
}
TOUR_MATCHES = {
    "ATP": "matches",
    "WTA": "wta_matches",
}

app = Flask(__name__)

# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Static pages ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)

# ── API: player search ────────────────────────────────────────────────────────
@app.route("/api/players/search")
def player_search():
    q       = request.args.get("q",       "").strip()
    tour    = request.args.get("tour",    "ATP").upper()
    surface = request.args.get("surface", "").strip()   # optional surface filter
    view    = TOUR_VIEW.get(tour, "player_surface_stats")
    if not q:
        return jsonify([])
    conn = get_conn()
    if surface:
        # Only return players who have recorded stats on the requested surface
        cur = conn.execute(
            f"SELECT DISTINCT player FROM {view}"
            " WHERE LOWER(player) LIKE LOWER(?)"
            "   AND surface = ?"
            " ORDER BY player LIMIT 15",
            (f"%{q}%", surface)
        )
    else:
        cur = conn.execute(
            f"SELECT DISTINCT player FROM {view}"
            " WHERE LOWER(player) LIKE LOWER(?)"
            " ORDER BY player LIMIT 15",
            (f"%{q}%",)
        )
    results = [r["player"] for r in cur.fetchall()]
    conn.close()
    return jsonify(results)

# ── API: rankings ─────────────────────────────────────────────────────────────
@app.route("/api/rankings")
def rankings():
    tour  = request.args.get("tour", "ATP").upper()
    limit = min(int(request.args.get("limit", 100)), 500)
    view  = TOUR_VIEW.get(tour, "player_surface_stats")
    conn  = get_conn()
    cur   = conn.execute(f"""
        SELECT
            player,
            SUM(match_count)     AS total_matches,
            AVG(serve_win_pct)   AS serve_win_pct,
            AVG(first_serve_pct) AS first_serve_pct,
            AVG(ace_rate)        AS ace_rate,
            AVG(bp_save_pct)     AS bp_save_pct
        FROM {view}
        GROUP BY player
        ORDER BY total_matches DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── API: rankings/extended (adds archetype label) ─────────────────────────────
@app.route("/api/rankings/extended")
def rankings_extended():
    tour  = request.args.get("tour", "ATP").upper()
    limit = min(int(request.args.get("limit", 100)), 500)
    view  = TOUR_VIEW.get(tour, "player_surface_stats")
    conn  = get_conn()
    cur   = conn.execute(f"""
        WITH base AS (
            SELECT
                player,
                SUM(match_count)  AS total_matches,
                SUM(serve_win_pct       * match_count) / SUM(match_count) AS power,
                SUM(ace_rate            * match_count) / SUM(match_count) AS danger,
                SUM(bp_save_pct         * match_count) / SUM(match_count) AS clutch,
                SUM(df_rate             * match_count) / SUM(match_count) AS df_rate,
                SUM(first_serve_pct     * match_count) / SUM(match_count) AS precision,
                SUM(first_serve_win_pct * match_count) / SUM(match_count) AS first_win,
                SUM(second_serve_win_pct* match_count) / SUM(match_count) AS second_win
            FROM {view}
            GROUP BY player
            ORDER BY total_matches DESC
            LIMIT ?
        )
        SELECT
            player,
            total_matches,
            ROUND(power   * 100, 1)  AS serve_win_pct,
            ROUND(danger  * 100, 2)  AS ace_rate,
            ROUND(clutch  * 100, 1)  AS bp_save_pct,
            CASE
                WHEN danger   > 0.08  AND first_win  > 0.78 THEN 'Big Server'
                WHEN clutch   > 0.65  AND power      > 0.64 THEN 'Iron Wall'
                WHEN second_win > 0.56 AND clutch    > 0.60 THEN 'Tactician'
                WHEN precision > 0.68  AND df_rate   < 0.02 THEN 'Precision Machine'
                WHEN power    > 0.66                         THEN 'All-Court Athlete'
                ELSE 'Grinder'
            END AS archetype
        FROM base
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── API: player profile ───────────────────────────────────────────────────────
@app.route("/api/player/<path:name>/card")
def player_card(name):
    """
    RPG-style stat card: attribute ratings, archetype, giant-killer score,
    grand-slam prestige, and recent form (last 10 matches).
    Route must be registered BEFORE /api/player/<path:name> so Flask matches
    the more-specific path first.
    """
    tour  = request.args.get("tour", "ATP").upper()
    view  = TOUR_VIEW.get(tour, "player_surface_stats")
    table = TOUR_MATCHES.get(tour, "matches")
    conn  = get_conn()

    # ── Attribute ratings + archetype ────────────────────────────────────────
    cur = conn.execute(f"""
        WITH stats AS (
            SELECT
                player, match_count,
                serve_win_pct, first_serve_pct, first_serve_win_pct,
                second_serve_win_pct, ace_rate, df_rate, bp_save_pct
            FROM {view}
            WHERE player = ?
        ),
        totals AS (
            SELECT
                player,
                SUM(match_count) AS total_matches,
                SUM(serve_win_pct        * match_count) / SUM(match_count) AS power,
                SUM(first_serve_pct      * match_count) / SUM(match_count) AS precision,
                SUM(bp_save_pct          * match_count) / SUM(match_count) AS clutch,
                SUM(ace_rate             * match_count) / SUM(match_count) AS danger,
                SUM(df_rate              * match_count) / SUM(match_count) AS df_r,
                SUM(first_serve_win_pct  * match_count) / SUM(match_count) AS first_win,
                SUM(second_serve_win_pct * match_count) / SUM(match_count) AS second_win,
                1.0 - SUM(df_rate * match_count) / SUM(match_count)        AS consistency
            FROM stats GROUP BY player
        )
        SELECT
            player,
            total_matches,
            ROUND(power       * 100, 1)  AS power_pct,
            ROUND(precision   * 100, 1)  AS precision_pct,
            ROUND(clutch      * 100, 1)  AS clutch_pct,
            ROUND(danger      * 100, 2)  AS danger_pct,
            ROUND(consistency * 100, 1)  AS consistency_pct,
            ROUND(
                (power * 40) + (precision * 20) + (clutch * 25) +
                (danger * 100) + (consistency * 15)
            , 1) AS overall_rating,
            CASE
                WHEN danger   > 0.08  AND first_win  > 0.78 THEN 'Big Server'
                WHEN clutch   > 0.65  AND power      > 0.64 THEN 'Iron Wall'
                WHEN second_win > 0.56 AND clutch    > 0.60 THEN 'Tactician'
                WHEN precision > 0.68  AND df_r      < 0.02 THEN 'Precision Machine'
                WHEN power    > 0.66                         THEN 'All-Court Athlete'
                ELSE 'Grinder'
            END AS archetype
        FROM totals
    """, (name,))
    row = cur.fetchone()
    if not row:
        conn.close()
        abort(404)
    card = dict(row)

    # ── Giant Killer score ────────────────────────────────────────────────────
    cur = conn.execute(f"""
        SELECT
            COUNT(*)                                              AS total_upsets,
            ROUND(AVG(loser_rank - winner_rank), 1)              AS avg_rank_gap,
            MAX(loser_rank - winner_rank)                        AS biggest_upset,
            ROUND(COUNT(*) * AVG(loser_rank - winner_rank) / 100.0, 2) AS giant_killer_score
        FROM {table}
        WHERE winner_name = ?
          AND match_type  = 'main'
          AND winner_rank IS NOT NULL
          AND loser_rank  IS NOT NULL
          AND (loser_rank - winner_rank) >= 20
    """, (name,))
    card["upsets"] = dict(cur.fetchone() or {})

    # ── Grand Slam prestige ───────────────────────────────────────────────────
    cur = conn.execute(f"""
        SELECT
            COUNT(*) AS slam_wins,
            ROUND(AVG(COALESCE(w_ace,    0) / NULLIF(w_svpt,    0)) * 100, 2) AS slam_ace_rate,
            ROUND(AVG(COALESCE(w_bpSaved,0) / NULLIF(w_bpFaced, 0)) * 100, 1) AS slam_clutch
        FROM {table}
        WHERE winner_name    = ?
          AND tourney_level  = 'G'
          AND match_type     = 'main'
          AND w_svpt > 0
    """, (name,))
    card["slams"] = dict(cur.fetchone() or {})

    # ── Recent form — last 10 matches ─────────────────────────────────────────
    cur = conn.execute(f"""
        WITH recent AS (
            SELECT tourney_date, 1 AS won FROM {table}
            WHERE winner_name = ? AND match_type = 'main'
            UNION ALL
            SELECT tourney_date, 0 AS won FROM {table}
            WHERE loser_name  = ? AND match_type = 'main'
        ),
        ranked AS (
            SELECT won,
                   ROW_NUMBER() OVER (ORDER BY tourney_date DESC) AS rn
            FROM recent
        )
        SELECT SUM(won) AS wins_last_10, COUNT(*) AS played
        FROM ranked WHERE rn <= 10
    """, (name, name))
    form_row  = cur.fetchone()
    wins      = int(form_row["wins_last_10"] or 0) if form_row else 0
    losses    = 10 - wins
    card["form"] = {
        "wins":   wins,
        "losses": losses,
        "label":  (
            "On Fire"     if wins >= 8 else
            "In Form"     if wins >= 6 else
            "Neutral"     if wins >= 4 else
            "Cold Streak"
        )
    }

    conn.close()
    return jsonify(card)


@app.route("/api/player/<path:name>")
def player_profile(name):
    tour  = request.args.get("tour", "ATP").upper()
    view  = TOUR_VIEW.get(tour, "player_surface_stats")
    table = TOUR_MATCHES.get(tour, "matches")
    conn  = get_conn()

    cur = conn.execute(
        f"SELECT * FROM {view} WHERE player = ? ORDER BY match_count DESC",
        (name,)
    )
    surfaces = [dict(r) for r in cur.fetchall()]

    cur = conn.execute(f"""
        SELECT tourney_name, loser_name  AS opponent, score, tourney_date, surface, 'W' AS result
          FROM {table} WHERE winner_name = ? AND match_type = 'main'
        UNION ALL
        SELECT tourney_name, winner_name AS opponent, score, tourney_date, surface, 'L' AS result
          FROM {table} WHERE loser_name  = ? AND match_type = 'main'
        ORDER BY tourney_date DESC LIMIT 10
    """, (name, name))
    recent = [dict(r) for r in cur.fetchall()]

    cur = conn.execute(f"""
        SELECT
            SUM(return_pts_won) * 1.0 / NULLIF(SUM(return_pts_total), 0) AS return_dominance,
            AVG(CASE WHEN total_games > 0 AND minutes > 0
                     THEN minutes * 1.0 / total_games ELSE NULL END)      AS grind_factor
        FROM (
            SELECT
                l_svpt - (COALESCE(l_1stWon, 0) + COALESCE(l_2ndWon, 0)) AS return_pts_won,
                l_svpt AS return_pts_total,
                minutes,
                COALESCE(w_SvGms, 0) + COALESCE(l_SvGms, 0) AS total_games
            FROM {table}
            WHERE winner_name = ? AND match_type = 'main' AND l_svpt IS NOT NULL
            UNION ALL
            SELECT
                w_svpt - (COALESCE(w_1stWon, 0) + COALESCE(w_2ndWon, 0)) AS return_pts_won,
                w_svpt AS return_pts_total,
                minutes,
                COALESCE(w_SvGms, 0) + COALESCE(l_SvGms, 0) AS total_games
            FROM {table}
            WHERE loser_name  = ? AND match_type = 'main' AND w_svpt IS NOT NULL
        )
    """, (name, name))
    adv_row  = cur.fetchone()
    advanced = dict(adv_row) if adv_row else {"return_dominance": None, "grind_factor": None}

    conn.close()
    if not surfaces:
        abort(404)
    return jsonify({
        "name": name, "tour": tour,
        "surfaces": surfaces, "recent_matches": recent, "advanced": advanced
    })

# ── API: head-to-head ─────────────────────────────────────────────────────────
@app.route("/api/h2h")
def head_to_head():
    p1    = request.args.get("p1",   "").strip()
    p2    = request.args.get("p2",   "").strip()
    tour  = request.args.get("tour", "ATP").upper()
    table = TOUR_MATCHES.get(tour, "matches")
    if not p1 or not p2:
        return jsonify({"error": "Both player names are required"}), 400

    conn = get_conn()

    # Query 1: per-surface win counts and latest date per surface.
    # Keeping this simple — no nested aggregates — avoids the SQLite
    # "misuse of aggregate function" error.
    cur = conn.execute(f"""
        SELECT
            surface,
            COUNT(*)                                          AS matches_played,
            SUM(CASE WHEN winner_name = ? THEN 1 ELSE 0 END) AS p1_wins,
            SUM(CASE WHEN winner_name = ? THEN 1 ELSE 0 END) AS p2_wins,
            MAX(tourney_date)                                 AS last_match
        FROM {table}
        WHERE match_type = 'main'
          AND ((winner_name = ? AND loser_name = ?)
            OR (winner_name = ? AND loser_name = ?))
        GROUP BY surface
        ORDER BY matches_played DESC
    """, (p1, p2, p1, p2, p2, p1))
    rows = [dict(r) for r in cur.fetchall()]

    # Query 2: tourney name of the single most-recent encounter (any surface).
    cur = conn.execute(f"""
        SELECT tourney_name, tourney_date
        FROM {table}
        WHERE match_type = 'main'
          AND ((winner_name = ? AND loser_name = ?)
            OR (winner_name = ? AND loser_name = ?))
        ORDER BY tourney_date DESC
        LIMIT 1
    """, (p1, p2, p2, p1))
    last_row   = cur.fetchone()
    last_tourney = last_row["tourney_name"] if last_row else None
    last_date    = last_row["tourney_date"] if last_row else None

    conn.close()

    # Attach last_tourney to the surface row that matches the most-recent date,
    # leaving other rows without it (frontend handles None gracefully).
    for r in rows:
        r["last_tourney"] = last_tourney if r["last_match"] == last_date else None

    totals = {
        "p1_wins": sum(r["p1_wins"]        for r in rows),
        "p2_wins": sum(r["p2_wins"]        for r in rows),
        "matches": sum(r["matches_played"] for r in rows),
    }
    return jsonify({"player1": p1, "player2": p2, "by_surface": rows, "totals": totals})

# ── API: simulate ─────────────────────────────────────────────────────────────
@app.route("/api/simulate", methods=["POST"])
def run_simulation():
    from simulator import simulate, TOUR_VIEW as tv
    data    = request.get_json(force=True)
    p1      = data.get("player1", "").strip()
    p2      = data.get("player2", "").strip()
    surface = data.get("surface", "Hard")
    best_of = int(data.get("best_of", 3))
    tour    = data.get("tour", "ATP").upper()
    if not p1 or not p2:
        return jsonify({"error": "Both player names are required"}), 400
    view   = tv.get(tour, "player_surface_stats")
    conn   = get_conn()
    result = simulate(p1, p2, surface, best_of, conn, view)
    conn.close()
    return jsonify(result)

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n  Court Analytics → http://localhost:{args.port}/\n")
    app.run(debug=True, port=args.port, use_reloader=False)