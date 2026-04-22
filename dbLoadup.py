"""
ATP Match Database Setup
========================
Loads all atp_matches_YYYY.csv files (1968-2024) into a single SQLite database
and creates the player_stats view used by the What-If simulator.

Usage:
    python setup_db.py [--data-dir /path/to/csvs] [--db atp.db]

Defaults to the current directory for CSVs and 'atp.db' for the output.
"""

import sqlite3
import csv
import glob
import os
import sys
import argparse
import time

# ── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Load ATP CSVs into SQLite")
parser.add_argument("--data-dir", default=".", help="Directory containing atp_matches_YYYY.csv files")
parser.add_argument("--db", default="atp.db", help="Output SQLite database path")
args = parser.parse_args()

DATA_DIR = args.data_dir
DB_PATH  = args.db

# ── Schema ────────────────────────────────────────────────────────────────────
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS matches (
    tourney_id        TEXT,
    tourney_name      TEXT,
    surface           TEXT,
    draw_size         INTEGER,
    tourney_level     TEXT,
    tourney_date      TEXT,
    match_num         INTEGER,
    winner_id         INTEGER,
    winner_seed       TEXT,
    winner_entry      TEXT,
    winner_name       TEXT,
    winner_hand       TEXT,
    winner_ht         REAL,
    winner_ioc        TEXT,
    winner_age        REAL,
    loser_id          INTEGER,
    loser_seed        TEXT,
    loser_entry       TEXT,
    loser_name        TEXT,
    loser_hand        TEXT,
    loser_ht          REAL,
    loser_ioc         TEXT,
    loser_age         REAL,
    score             TEXT,
    best_of           INTEGER,
    round             TEXT,
    minutes           REAL,
    w_ace             REAL,
    w_df              REAL,
    w_svpt            REAL,
    w_1stIn           REAL,
    w_1stWon          REAL,
    w_2ndWon          REAL,
    w_SvGms           REAL,
    w_bpSaved         REAL,
    w_bpFaced         REAL,
    l_ace             REAL,
    l_df              REAL,
    l_svpt            REAL,
    l_1stIn           REAL,
    l_1stWon          REAL,
    l_2ndWon          REAL,
    l_SvGms           REAL,
    l_bpSaved         REAL,
    l_bpFaced         REAL,
    winner_rank       REAL,
    winner_rank_points REAL,
    loser_rank        REAL,
    loser_rank_points  REAL
);
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_winner ON matches(winner_name);
CREATE INDEX IF NOT EXISTS idx_loser  ON matches(loser_name);
CREATE INDEX IF NOT EXISTS idx_surface ON matches(surface);
CREATE INDEX IF NOT EXISTS idx_date   ON matches(tourney_date);
"""

# ── Aggregated player stats view (winner perspective only — rich stat rows) ───
# We only pull stats from rows where the player was the winner because loser
# stats are often noisier and many early-era rows have NULL loser serve data.
# We UNION with loser rows so both sides contribute to sample sizes.
CREATE_STATS_VIEW = """
CREATE VIEW IF NOT EXISTS player_surface_stats AS
WITH all_serve AS (
    -- when player won
    SELECT
        winner_name           AS player,
        surface,
        w_svpt  AS svpt,
        w_1stIn AS first_in,
        w_1stWon AS first_won,
        w_2ndWon AS second_won,
        w_ace   AS ace,
        w_df    AS df,
        w_bpSaved AS bp_saved,
        w_bpFaced AS bp_faced
    FROM matches
    WHERE w_svpt > 0 AND w_1stIn IS NOT NULL

    UNION ALL

    -- when player lost
    SELECT
        loser_name            AS player,
        surface,
        l_svpt  AS svpt,
        l_1stIn AS first_in,
        l_1stWon AS first_won,
        l_2ndWon AS second_won,
        l_ace   AS ace,
        l_df    AS df,
        l_bpSaved AS bp_saved,
        l_bpFaced AS bp_faced
    FROM matches
    WHERE l_svpt > 0 AND l_1stIn IS NOT NULL
)
SELECT
    player,
    surface,
    COUNT(*)                                                          AS match_count,
    -- core serve win probability (points won on serve / total serve points)
    AVG(CAST(first_won + second_won AS REAL) / NULLIF(svpt, 0))      AS serve_win_pct,
    -- 1st serve percentage
    AVG(CAST(first_in AS REAL) / NULLIF(svpt, 0))                    AS first_serve_pct,
    -- 1st serve win rate (given serve went in)
    AVG(CAST(first_won AS REAL) / NULLIF(first_in, 0))               AS first_serve_win_pct,
    -- 2nd serve win rate
    AVG(CAST(second_won AS REAL) / NULLIF(svpt - first_in, 0))       AS second_serve_win_pct,
    -- ace rate
    AVG(CAST(ace AS REAL) / NULLIF(svpt, 0))                         AS ace_rate,
    -- double fault rate
    AVG(CAST(df AS REAL) / NULLIF(svpt, 0))                          AS df_rate,
    -- break point save rate
    AVG(CAST(bp_saved AS REAL) / NULLIF(bp_faced, 0))                AS bp_save_pct
FROM all_serve
GROUP BY player, surface
HAVING match_count >= 5;   -- filter out players with near-zero data
"""

# ── Helpers ───────────────────────────────────────────────────────────────────
EXPECTED_COLS = {
    "tourney_id","tourney_name","surface","draw_size","tourney_level",
    "tourney_date","match_num","winner_id","winner_seed","winner_entry",
    "winner_name","winner_hand","winner_ht","winner_ioc","winner_age",
    "loser_id","loser_seed","loser_entry","loser_name","loser_hand",
    "loser_ht","loser_ioc","loser_age","score","best_of","round","minutes",
    "w_ace","w_df","w_svpt","w_1stIn","w_1stWon","w_2ndWon","w_SvGms",
    "w_bpSaved","w_bpFaced","l_ace","l_df","l_svpt","l_1stIn","l_1stWon",
    "l_2ndWon","l_SvGms","l_bpSaved","l_bpFaced",
    "winner_rank","winner_rank_points","loser_rank","loser_rank_points",
}

def safe(val):
    """Return None for blank/whitespace strings."""
    v = val.strip()
    return None if v == "" else v

def load_csv(conn, path):
    rows_inserted = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        if not EXPECTED_COLS.issubset(cols):
            missing = EXPECTED_COLS - cols
            print(f"  ⚠  Skipping {os.path.basename(path)} — missing cols: {missing}")
            return 0

        placeholder = ",".join(["?"] * len(EXPECTED_COLS))
        col_order = [
            "tourney_id","tourney_name","surface","draw_size","tourney_level",
            "tourney_date","match_num","winner_id","winner_seed","winner_entry",
            "winner_name","winner_hand","winner_ht","winner_ioc","winner_age",
            "loser_id","loser_seed","loser_entry","loser_name","loser_hand",
            "loser_ht","loser_ioc","loser_age","score","best_of","round","minutes",
            "w_ace","w_df","w_svpt","w_1stIn","w_1stWon","w_2ndWon","w_SvGms",
            "w_bpSaved","w_bpFaced","l_ace","l_df","l_svpt","l_1stIn","l_1stWon",
            "l_2ndWon","l_SvGms","l_bpSaved","l_bpFaced",
            "winner_rank","winner_rank_points","loser_rank","loser_rank_points",
        ]
        sql = f"INSERT INTO matches ({','.join(col_order)}) VALUES ({placeholder})"

        batch = []
        for row in reader:
            batch.append(tuple(safe(row.get(c, "")) for c in col_order))
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                rows_inserted += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows_inserted += len(batch)

    return rows_inserted

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    pattern = os.path.join(DATA_DIR, "atp_matches_[0-9][0-9][0-9][0-9].csv")
    csv_files = sorted(glob.glob(pattern))

    if not csv_files:
        print(f"No atp_matches_YYYY.csv files found in '{DATA_DIR}'")
        print("Run: python setup_db.py --data-dir /path/to/tennis-data")
        sys.exit(1)

    print(f"Found {len(csv_files)} annual CSV files → loading into '{DB_PATH}'")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(CREATE_TABLE)
    conn.commit()

    total = 0
    t0 = time.time()
    for path in csv_files:
        year = os.path.basename(path).replace("atp_matches_","").replace(".csv","")
        n = load_csv(conn, path)
        conn.commit()
        total += n
        print(f"  {year}: {n:,} rows")

    print(f"\nCreating indexes…")
    for stmt in CREATE_INDEX.strip().split("\n"):
        conn.execute(stmt)

    print("Creating player_surface_stats view…")
    conn.execute(CREATE_STATS_VIEW)
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    print(f"\n✓ Done — {total:,} matches loaded in {elapsed:.1f}s")
    print(f"  Database: {DB_PATH}")
    print(f"\nNext step:\n  python simulator.py --db {DB_PATH}")

if __name__ == "__main__":
    main()