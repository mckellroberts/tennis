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
    q    = request.args.get("q", "").strip()
    tour = request.args.get("tour", "ATP").upper()
    view = TOUR_VIEW.get(tour, "player_surface_stats")
    if not q:
        return jsonify([])
    conn = get_conn()
    cur  = conn.execute(
        f"SELECT DISTINCT player FROM {view}"
        " WHERE LOWER(player) LIKE LOWER(?) ORDER BY player LIMIT 15",
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

# ── API: player profile ───────────────────────────────────────────────────────
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
            AVG(CASE WHEN total_games > 0 AND minutes > 0 THEN minutes * 1.0 / total_games ELSE NULL END) AS grind_factor
        FROM (
            SELECT 
                l_svpt - (COALESCE(l_1stWon, 0) + COALESCE(l_2ndWon, 0)) AS return_pts_won,
                l_svpt AS return_pts_total,
                minutes,
                COALESCE(w_SvGms, 0) + COALESCE(l_SvGms, 0) AS total_games
            FROM {table} WHERE winner_name = ? AND match_type = 'main' AND l_svpt IS NOT NULL
            UNION ALL
            SELECT 
                w_svpt - (COALESCE(w_1stWon, 0) + COALESCE(w_2ndWon, 0)) AS return_pts_won,
                w_svpt AS return_pts_total,
                minutes,
                COALESCE(w_SvGms, 0) + COALESCE(l_SvGms, 0) AS total_games
            FROM {table} WHERE loser_name = ? AND match_type = 'main' AND w_svpt IS NOT NULL
        )
    """, (name, name))
    adv_row = cur.fetchone()
    advanced = dict(adv_row) if adv_row else {"return_dominance": None, "grind_factor": None}

    conn.close()
    if not surfaces:
        abort(404)
    return jsonify({"name": name, "tour": tour, "surfaces": surfaces, "recent_matches": recent, "advanced": advanced})

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
