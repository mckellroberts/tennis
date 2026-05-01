"""
Court Analytics — Flask API server
====================================
Serves the frontend and provides API endpoints backed by the normalised SQLite DB.

Schema tables used:
    Tournament, Player, Match, PlayerMatchStats, PlayerTournamentStats
    player_surface_stats  (materialised table, not a view)
    Rankings              (view over PlayerTournamentStats + Tournament + Player)

Usage:
    python server.py            # http://localhost:5000
    python server.py --port 8080
    python server.py --db tennis.db
"""

import os
import argparse
import sqlite3

from flask import Flask, jsonify, request, send_from_directory, abort

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Court Analytics server")
parser.add_argument("--db",   default="tennis.db")
parser.add_argument("--port", default=5000, type=int)
args, _ = parser.parse_known_args()

BASE         = os.path.dirname(__file__)
DB_PATH      = os.path.join(BASE, args.db)
FRONTEND_DIR = os.path.join(BASE, "frontend")

# Both tours share one table set — differentiated by Player.gender
TOUR_GENDER = {
    "ATP": "M",
    "WTA": "F",
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

# ── API: player search ─────────────────────────────────────────────────────────
@app.route("/api/players/search")
def player_search():
    q       = request.args.get("q",       "").strip()
    tour    = request.args.get("tour",    "ATP").upper()
    surface = request.args.get("surface", "").strip()
    gender  = TOUR_GENDER.get(tour, "M")

    if not q:
        return jsonify([])

    conn = get_conn()
    if surface:
        cur = conn.execute("""
            SELECT DISTINCT pss.player
            FROM player_surface_stats pss
            JOIN Player p ON p.id = pss.player_id
            WHERE LOWER(pss.player) LIKE LOWER(?)
              AND pss.surface = ?
              AND p.gender    = ?
            ORDER BY pss.player LIMIT 15
        """, (f"%{q}%", surface, gender))
    else:
        cur = conn.execute("""
            SELECT DISTINCT pss.player
            FROM player_surface_stats pss
            JOIN Player p ON p.id = pss.player_id
            WHERE LOWER(pss.player) LIKE LOWER(?)
              AND p.gender = ?
            ORDER BY pss.player LIMIT 15
        """, (f"%{q}%", gender))

    results = [r["player"] for r in cur.fetchall()]
    conn.close()
    return jsonify(results)

# ── API: rankings ──────────────────────────────────────────────────────────────
@app.route("/api/rankings")
def rankings():
    tour   = request.args.get("tour",  "ATP").upper()
    limit  = min(int(request.args.get("limit", 100)), 500)
    gender = TOUR_GENDER.get(tour, "M")
    conn   = get_conn()
    cur    = conn.execute("""
        SELECT
            pss.player,
            SUM(pss.match_count)     AS total_matches,
            AVG(pss.serve_win_pct)   AS serve_win_pct,
            AVG(pss.first_serve_pct) AS first_serve_pct,
            AVG(pss.ace_rate)        AS ace_rate,
            AVG(pss.bp_save_pct)     AS bp_save_pct
        FROM player_surface_stats pss
        JOIN Player p ON p.id = pss.player_id
        WHERE p.gender = ?
        GROUP BY pss.player_id
        ORDER BY total_matches DESC
        LIMIT ?
    """, (gender, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)

# ── API: rankings/extended (adds archetype label) ──────────────────────────────
@app.route("/api/rankings/extended")
def rankings_extended():
    tour   = request.args.get("tour",  "ATP").upper()
    limit  = min(int(request.args.get("limit", 100)), 500)
    gender = TOUR_GENDER.get(tour, "M")
    conn   = get_conn()
    cur    = conn.execute("""
        WITH base AS (
            SELECT
                pss.player_id,
                pss.player,
                SUM(pss.match_count)                                                    AS total_matches,
                SUM(pss.serve_win_pct        * pss.match_count) / SUM(pss.match_count) AS power,
                SUM(pss.ace_rate             * pss.match_count) / SUM(pss.match_count) AS danger,
                SUM(pss.bp_save_pct          * pss.match_count) / SUM(pss.match_count) AS clutch,
                SUM(pss.df_rate              * pss.match_count) / SUM(pss.match_count) AS df_rate,
                SUM(pss.first_serve_pct      * pss.match_count) / SUM(pss.match_count) AS precision,
                SUM(pss.first_serve_win_pct  * pss.match_count) / SUM(pss.match_count) AS first_win,
                SUM(pss.second_serve_win_pct * pss.match_count) / SUM(pss.match_count) AS second_win
            FROM player_surface_stats pss
            JOIN Player p ON p.id = pss.player_id
            WHERE p.gender = ?
            GROUP BY pss.player_id
            ORDER BY total_matches DESC
            LIMIT ?
        )
        SELECT
            player_id, player, total_matches,
            ROUND(power   * 100, 1) AS serve_win_pct,
            ROUND(danger  * 100, 2) AS ace_rate,
            ROUND(clutch  * 100, 1) AS bp_save_pct,
            CASE
                WHEN danger    > 0.08  AND first_win  > 0.78 THEN 'Big Server'
                WHEN clutch    > 0.65  AND power      > 0.64 THEN 'Iron Wall'
                WHEN second_win > 0.56 AND clutch     > 0.60 THEN 'Tactician'
                WHEN precision > 0.68  AND df_rate    < 0.02 THEN 'Precision Machine'
                WHEN power     > 0.66                         THEN 'All-Court Athlete'
                ELSE 'Grinder'
            END AS archetype
        FROM base
    """, (gender, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


# ── API: fun queries ───────────────────────────────────────────────────────────
@app.route("/api/player/<path:name>/fun")
def player_fun(name):
    """
    Four interesting queries that reveal unusual things about a player's career.
    Registered before /api/player/<name> so Flask matches the longer path first.
    """
    tour   = request.args.get("tour", "ATP").upper()
    gender = TOUR_GENDER.get(tour, "M")
    conn   = get_conn()

    cur = conn.execute(
        "SELECT id FROM Player WHERE name = ? AND gender = ?", (name, gender)
    )
    player_row = cur.fetchone()
    if not player_row:
        conn.close()
        abort(404)
    player_id = player_row["id"]

    result = {}

    # ── 1. Best Career Win ─────────────────────────────────────────────────────
    # The single highest-ranked (lowest rank number) opponent ever beaten
    # in a main-draw match, identified via PlayerTournamentStats.
    cur = conn.execute("""
        SELECT
            opp.name                  AS opponent,
            pts.rank                  AS opponent_rank,
            t.name                    AS tournament,
            t.date                    AS match_date
        FROM Match m
        JOIN Player     opp ON opp.id          = m.loser_id
        JOIN Tournament t   ON t.id            = m.tournament_id
        JOIN PlayerTournamentStats pts
                            ON pts.tournament_id = m.tournament_id
                           AND pts.player_id     = m.loser_id
        WHERE m.winner_id  = ?
          AND m.match_type = 'main'
          AND pts.rank     IS NOT NULL
        ORDER BY pts.rank ASC
        LIMIT 1
    """, (player_id,))
    row = cur.fetchone()
    result["best_win"] = dict(row) if row else None

    # ── 2. Heated Rival ───────────────────────────────────────────────────────
    # The opponent they have the most history with, weighted by:
    #   0.50 — total matches (most-played gets the base score)
    #   0.35 — closeness: fraction of each match's sets that went the distance
    #            proxy = sets_played / best_of, averaged over all meetings.
    #            A 7-6 6-7 7-6 three-setter scores 1.0; a 6-1 6-0 scores 0.33.
    #            Sets played = number of space characters in score + 1.
    #   0.15 — recency: average julianday of their meetings, normalised so
    #            older matchups don't dominate over active rivalries.
    #
    # Minimum 10 matches; returns NULL if no opponent clears the threshold.
    # The three weights are normalised inside the CTE so the rival_score
    # is always 0–1 regardless of career length.
    cur = conn.execute("""
        WITH all_matches AS (
            SELECT
                CASE WHEN m.winner_id = ? THEN m.loser_id
                     ELSE m.winner_id END                               AS opp_id,
                CASE WHEN m.winner_id = ? THEN 1 ELSE 0 END            AS player_won,
                (length(m.score) - length(replace(m.score, ' ', '')) + 1) AS sets_played,
                COALESCE(m.best_of, 3)                                  AS best_of,
                julianday(
                    substr(t.date,1,4) || '-' ||
                    substr(t.date,5,2) || '-' ||
                    substr(t.date,7,2)
                )                                                       AS jday
            FROM Match m
            JOIN Tournament t ON t.id = m.tournament_id
            WHERE (m.winner_id = ? OR m.loser_id = ?)
              AND m.match_type = 'main'
              AND m.score IS NOT NULL
        ),
        h2h_stats AS (
            SELECT
                opp_id,
                COUNT(*)                                      AS total_matches,
                SUM(player_won)                               AS player_wins,
                AVG(CAST(sets_played AS REAL) / best_of)     AS avg_closeness,
                AVG(jday)                                     AS avg_jday
            FROM all_matches
            GROUP BY opp_id
            HAVING total_matches >= 10
        ),
        normalised AS (
            SELECT
                opp_id,
                total_matches,
                player_wins,
                avg_closeness,
                avg_jday,
                (0.50 * CASE WHEN MAX(total_matches) OVER () > MIN(total_matches) OVER ()
                              THEN (total_matches - MIN(total_matches) OVER ()) * 1.0
                                   / (MAX(total_matches) OVER () - MIN(total_matches) OVER ())
                              ELSE 1.0 END)
                + (0.35 * avg_closeness)
                + (0.15 * CASE WHEN MAX(avg_jday) OVER () > MIN(avg_jday) OVER ()
                                THEN (avg_jday - MIN(avg_jday) OVER ()) * 1.0
                                     / (MAX(avg_jday) OVER () - MIN(avg_jday) OVER ())
                                ELSE 1.0 END)               AS rival_score
            FROM h2h_stats
        )
        SELECT
            p.name                              AS name,
            n.total_matches                     AS matches,
            n.player_wins                       AS player_wins,
            ROUND(n.avg_closeness, 2)           AS closeness,
            ROUND(n.rival_score,   3)           AS rival_score
        FROM normalised n
        JOIN Player p ON p.id = n.opp_id
        ORDER BY rival_score DESC
        LIMIT 1
    """, (player_id, player_id, player_id, player_id))
    row = cur.fetchone()
    result["nemesis"] = dict(row) if row else None

    # ── 3. Longest Marathon ────────────────────────────────────────────────────
    # Their single longest main-draw match by recorded minutes.
    cur = conn.execute("""
        SELECT
            m.minutes,
            t.name                                              AS tournament,
            opp.name                                            AS opponent,
            m.score,
            t.surface,
            CASE WHEN m.winner_id = ? THEN 'W' ELSE 'L' END    AS result
        FROM Match m
        JOIN Tournament t   ON t.id   = m.tournament_id
        JOIN Player     opp ON opp.id = CASE
                                          WHEN m.winner_id = ? THEN m.loser_id
                                          ELSE m.winner_id
                                        END
        WHERE (m.winner_id = ? OR m.loser_id = ?)
          AND m.match_type = 'main'
          AND m.minutes    IS NOT NULL
        ORDER BY m.minutes DESC
        LIMIT 1
    """, (player_id, player_id, player_id, player_id))
    row = cur.fetchone()
    result["marathon"] = dict(row) if row else None

    # ── 4. Happy Hunting Ground ────────────────────────────────────────────────
    # The tournament where they have the highest win rate (min 5 appearances).
    cur = conn.execute("""
        WITH records AS (
            SELECT
                t.name                                            AS tournament,
                SUM(CASE WHEN m.winner_id = ? THEN 1 ELSE 0 END) AS wins,
                COUNT(*)                                          AS total
            FROM Match m
            JOIN Tournament t ON t.id = m.tournament_id
            WHERE (m.winner_id = ? OR m.loser_id = ?)
              AND m.match_type = 'main'
            GROUP BY t.name
            HAVING total >= 5
        )
        SELECT tournament, wins, total,
               ROUND(wins * 100.0 / total, 0) AS win_pct
        FROM records
        ORDER BY win_pct DESC, wins DESC
        LIMIT 1
    """, (player_id, player_id, player_id))
    row = cur.fetchone()
    result["hunting_ground"] = dict(row) if row else None

    conn.close()
    return jsonify(result)

# ── API: player card ───────────────────────────────────────────────────────────
# Registered BEFORE /api/player/<name> so Flask matches the longer path first.
@app.route("/api/player/<path:name>/card")
def player_card(name):
    tour   = request.args.get("tour", "ATP").upper()
    gender = TOUR_GENDER.get(tour, "M")
    conn   = get_conn()

    # ── Attribute ratings + archetype ─────────────────────────────────────────
    cur = conn.execute("""
        WITH stats AS (
            SELECT pss.player_id, pss.match_count,
                   pss.serve_win_pct, pss.first_serve_pct, pss.first_serve_win_pct,
                   pss.second_serve_win_pct, pss.ace_rate, pss.df_rate, pss.bp_save_pct
            FROM player_surface_stats pss
            JOIN Player p ON p.id = pss.player_id
            WHERE pss.player = ? AND p.gender = ?
        ),
        totals AS (
            SELECT
                player_id,
                SUM(match_count)                                              AS total_matches,
                SUM(serve_win_pct        * match_count) / SUM(match_count)   AS power,
                SUM(first_serve_pct      * match_count) / SUM(match_count)   AS precision,
                SUM(bp_save_pct          * match_count) / SUM(match_count)   AS clutch,
                SUM(ace_rate             * match_count) / SUM(match_count)   AS danger,
                SUM(df_rate              * match_count) / SUM(match_count)   AS df_r,
                SUM(first_serve_win_pct  * match_count) / SUM(match_count)   AS first_win,
                SUM(second_serve_win_pct * match_count) / SUM(match_count)   AS second_win,
                1.0 - SUM(df_rate * match_count) / SUM(match_count)          AS consistency
            FROM stats GROUP BY player_id
        )
        SELECT
            player_id, total_matches,
            ROUND(power       * 100, 1) AS power_pct,
            ROUND(precision   * 100, 1) AS precision_pct,
            ROUND(clutch      * 100, 1) AS clutch_pct,
            ROUND(danger      * 100, 2) AS danger_pct,
            ROUND(consistency * 100, 1) AS consistency_pct,
            ROUND(
                (power*40) + (precision*20) + (clutch*25) +
                (danger*100) + (consistency*15)
            , 1) AS overall_rating,
            CASE
                WHEN danger    > 0.08  AND first_win > 0.78 THEN 'Big Server'
                WHEN clutch    > 0.65  AND power     > 0.64 THEN 'Iron Wall'
                WHEN second_win > 0.56 AND clutch    > 0.60 THEN 'Tactician'
                WHEN precision > 0.68  AND df_r      < 0.02 THEN 'Precision Machine'
                WHEN power     > 0.66                        THEN 'All-Court Athlete'
                ELSE 'Grinder'
            END AS archetype
        FROM totals
    """, (name, gender))
    row = cur.fetchone()
    if not row:
        conn.close()
        abort(404)
    card      = dict(row)
    player_id = card["player_id"]

    # ── Giant Killer — rank data now in PlayerTournamentStats ─────────────────
    cur = conn.execute("""
        SELECT
            COUNT(*)                                                        AS total_upsets,
            ROUND(AVG(l_pts.rank - w_pts.rank), 1)                        AS avg_rank_gap,
            MAX(l_pts.rank - w_pts.rank)                                   AS biggest_upset,
            ROUND(COUNT(*) * AVG(l_pts.rank - w_pts.rank) / 100.0, 2)    AS giant_killer_score
        FROM Match m
        JOIN PlayerTournamentStats w_pts ON w_pts.tournament_id = m.tournament_id
                                        AND w_pts.player_id    = m.winner_id
        JOIN PlayerTournamentStats l_pts ON l_pts.tournament_id = m.tournament_id
                                        AND l_pts.player_id    = m.loser_id
        WHERE m.winner_id  = ?
          AND m.match_type = 'main'
          AND w_pts.rank   IS NOT NULL
          AND l_pts.rank   IS NOT NULL
          AND (l_pts.rank - w_pts.rank) >= 20
    """, (player_id,))
    card["upsets"] = dict(cur.fetchone() or {})

    # ── Grand Slam prestige — per-player stats from PlayerMatchStats ──────────
    cur = conn.execute("""
        SELECT
            COUNT(*) AS slam_wins,
            ROUND(AVG(COALESCE(pms.ace,      0) / NULLIF(pms.svpt,     0)) * 100, 2) AS slam_ace_rate,
            ROUND(AVG(COALESCE(pms.bp_saved, 0) / NULLIF(pms.bp_faced, 0)) * 100, 1) AS slam_clutch
        FROM Match m
        JOIN Tournament t     ON t.id           = m.tournament_id
        JOIN PlayerMatchStats pms ON pms.tournament_id = m.tournament_id
                                AND pms.match_num     = m.match_num
                                AND pms.player_id     = m.winner_id
        WHERE m.winner_id  = ?
          AND t.level       = 'G'
          AND m.match_type  = 'main'
          AND pms.svpt > 0
    """, (player_id,))
    card["slams"] = dict(cur.fetchone() or {})

    # ── Recent form — last 10 main-draw matches ───────────────────────────────
    cur = conn.execute("""
        WITH recent AS (
            SELECT t.date AS tourney_date, 1 AS won
            FROM Match m
            JOIN Tournament t ON t.id = m.tournament_id
            WHERE m.winner_id = ? AND m.match_type = 'main'
            UNION ALL
            SELECT t.date AS tourney_date, 0 AS won
            FROM Match m
            JOIN Tournament t ON t.id = m.tournament_id
            WHERE m.loser_id = ? AND m.match_type = 'main'
        ),
        ranked AS (
            SELECT won,
                   ROW_NUMBER() OVER (ORDER BY tourney_date DESC) AS rn
            FROM recent
        )
        SELECT SUM(won) AS wins_last_10, COUNT(*) AS played
        FROM ranked WHERE rn <= 10
    """, (player_id, player_id))
    form_row = cur.fetchone()
    wins = int(form_row["wins_last_10"] or 0) if form_row else 0
    card["form"] = {
        "wins":   wins,
        "losses": 10 - wins,
        "label": (
            "On Fire"     if wins >= 8 else
            "In Form"     if wins >= 6 else
            "Neutral"     if wins >= 4 else
            "Cold Streak"
        ),
    }

    conn.close()
    return jsonify(card)

# ── API: player profile ────────────────────────────────────────────────────────
@app.route("/api/player/<path:name>")
def player_profile(name):
    tour   = request.args.get("tour", "ATP").upper()
    gender = TOUR_GENDER.get(tour, "M")
    conn   = get_conn()

    cur = conn.execute(
        "SELECT id FROM Player WHERE name = ? AND gender = ?", (name, gender)
    )
    player_row = cur.fetchone()
    if not player_row:
        conn.close()
        abort(404)
    player_id = player_row["id"]

    # Surface stats
    cur = conn.execute(
        "SELECT * FROM player_surface_stats WHERE player_id = ? ORDER BY match_count DESC",
        (player_id,)
    )
    surfaces = [dict(r) for r in cur.fetchall()]

    # Recent matches — join through Player for opponent name, Tournament for meta
    cur = conn.execute("""
        SELECT t.name AS tourney_name, opp.name AS opponent,
               m.score, t.date AS tourney_date, t.surface, 'W' AS result
        FROM Match m
        JOIN Tournament t ON t.id   = m.tournament_id
        JOIN Player opp   ON opp.id = m.loser_id
        WHERE m.winner_id = ? AND m.match_type = 'main'

        UNION ALL

        SELECT t.name AS tourney_name, opp.name AS opponent,
               m.score, t.date AS tourney_date, t.surface, 'L' AS result
        FROM Match m
        JOIN Tournament t ON t.id   = m.tournament_id
        JOIN Player opp   ON opp.id = m.winner_id
        WHERE m.loser_id  = ? AND m.match_type = 'main'

        ORDER BY tourney_date DESC LIMIT 10
    """, (player_id, player_id))
    recent = [dict(r) for r in cur.fetchall()]

    # Advanced: return dominance and grind factor
    # Return points won = opponent's serve points they did NOT convert
    cur = conn.execute("""
        SELECT
            SUM(rpts_won) * 1.0 / NULLIF(SUM(rpts_total), 0) AS return_dominance,
            AVG(CASE WHEN total_games > 0 AND minutes > 0
                     THEN minutes * 1.0 / total_games ELSE NULL END) AS grind_factor
        FROM (
            SELECT
                opp.svpt - (COALESCE(opp.first_won, 0) + COALESCE(opp.second_won, 0)) AS rpts_won,
                opp.svpt                                                                AS rpts_total,
                m.minutes,
                COALESCE(me.sv_gms, 0) + COALESCE(opp.sv_gms, 0)                      AS total_games
            FROM Match m
            JOIN PlayerMatchStats me  ON me.tournament_id  = m.tournament_id
                                     AND me.match_num      = m.match_num
                                     AND me.player_id      = m.winner_id
            JOIN PlayerMatchStats opp ON opp.tournament_id = m.tournament_id
                                     AND opp.match_num     = m.match_num
                                     AND opp.player_id     = m.loser_id
            WHERE m.winner_id  = ? AND m.match_type = 'main' AND opp.svpt IS NOT NULL

            UNION ALL

            SELECT
                opp.svpt - (COALESCE(opp.first_won, 0) + COALESCE(opp.second_won, 0)) AS rpts_won,
                opp.svpt                                                                AS rpts_total,
                m.minutes,
                COALESCE(opp.sv_gms, 0) + COALESCE(me.sv_gms, 0)                      AS total_games
            FROM Match m
            JOIN PlayerMatchStats me  ON me.tournament_id  = m.tournament_id
                                     AND me.match_num      = m.match_num
                                     AND me.player_id      = m.loser_id
            JOIN PlayerMatchStats opp ON opp.tournament_id = m.tournament_id
                                     AND opp.match_num     = m.match_num
                                     AND opp.player_id     = m.winner_id
            WHERE m.loser_id   = ? AND m.match_type = 'main' AND opp.svpt IS NOT NULL
        )
    """, (player_id, player_id))
    adv_row  = cur.fetchone()
    advanced = dict(adv_row) if adv_row else {"return_dominance": None, "grind_factor": None}

    conn.close()
    if not surfaces:
        abort(404)
    return jsonify({
        "name": name, "tour": tour,
        "surfaces": surfaces, "recent_matches": recent, "advanced": advanced,
    })

# ── API: head-to-head ──────────────────────────────────────────────────────────
@app.route("/api/h2h")
def head_to_head():
    p1     = request.args.get("p1",   "").strip()
    p2     = request.args.get("p2",   "").strip()
    tour   = request.args.get("tour", "ATP").upper()
    gender = TOUR_GENDER.get(tour, "M")

    if not p1 or not p2:
        return jsonify({"error": "Both player names are required"}), 400

    conn = get_conn()

    # Per-surface counts — resolve both player IDs once in a CTE then CROSS JOIN
    cur = conn.execute("""
        WITH player_ids AS (
            SELECT MAX(CASE WHEN name = ? THEN id END) AS p1_id,
                   MAX(CASE WHEN name = ? THEN id END) AS p2_id
            FROM Player
            WHERE name IN (?, ?) AND gender = ?
        )
        SELECT
            t.surface,
            COUNT(*)                                                   AS matches_played,
            SUM(CASE WHEN m.winner_id = pi.p1_id THEN 1 ELSE 0 END)   AS p1_wins,
            SUM(CASE WHEN m.winner_id = pi.p2_id THEN 1 ELSE 0 END)   AS p2_wins,
            MAX(t.date)                                                AS last_match
        FROM Match m
        JOIN Tournament t ON t.id = m.tournament_id
        CROSS JOIN player_ids pi
        WHERE m.match_type = 'main'
          AND ((m.winner_id = pi.p1_id AND m.loser_id = pi.p2_id)
            OR (m.winner_id = pi.p2_id AND m.loser_id = pi.p1_id))
        GROUP BY t.surface
        ORDER BY matches_played DESC
    """, (p1, p2, p1, p2, gender))
    rows = [dict(r) for r in cur.fetchall()]

    # Most recent encounter — separate query avoids nested MAX()
    cur = conn.execute("""
        WITH player_ids AS (
            SELECT MAX(CASE WHEN name = ? THEN id END) AS p1_id,
                   MAX(CASE WHEN name = ? THEN id END) AS p2_id
            FROM Player WHERE name IN (?, ?) AND gender = ?
        )
        SELECT t.name AS tourney_name, t.date AS tourney_date
        FROM Match m
        JOIN Tournament t ON t.id = m.tournament_id
        CROSS JOIN player_ids pi
        WHERE m.match_type = 'main'
          AND ((m.winner_id = pi.p1_id AND m.loser_id = pi.p2_id)
            OR (m.winner_id = pi.p2_id AND m.loser_id = pi.p1_id))
        ORDER BY t.date DESC LIMIT 1
    """, (p1, p2, p1, p2, gender))
    last_row     = cur.fetchone()
    last_tourney = last_row["tourney_name"] if last_row else None
    last_date    = last_row["tourney_date"]  if last_row else None

    conn.close()

    for r in rows:
        r["last_tourney"] = last_tourney if r["last_match"] == last_date else None
        r["p1_wins"]      = r["p1_wins"] or 0
        r["p2_wins"]      = r["p2_wins"] or 0

    totals = {
        "p1_wins": sum(r["p1_wins"] for r in rows),
        "p2_wins": sum(r["p2_wins"] for r in rows),
        "matches": sum(r["matches_played"] for r in rows),
    }
    return jsonify({"player1": p1, "player2": p2, "by_surface": rows, "totals": totals})

# ── API: simulate ──────────────────────────────────────────────────────────────
@app.route("/api/simulate", methods=["POST"])
def run_simulation():
    from simulator import simulate
    data    = request.get_json(force=True)
    p1      = data.get("player1", "").strip()
    p2      = data.get("player2", "").strip()
    surface = data.get("surface", "Hard")
    best_of = int(data.get("best_of", 3))
    if not p1 or not p2:
        return jsonify({"error": "Both player names are required"}), 400
    conn   = get_conn()
    result = simulate(p1, p2, surface, best_of, conn)
    conn.close()
    return jsonify(result)

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n  Court Analytics → http://localhost:{args.port}/\n")
    app.run(debug=True, port=args.port, use_reloader=False)