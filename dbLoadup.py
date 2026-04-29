"""
Tennis Match Database Setup
============================
Loads all ATP and WTA CSV files into a single SQLite database:

  ATP:
  - atp_matches_YYYY.csv          → matches (match_type='main')
  - atp_matches_qual_chall_*.csv  → matches (match_type='qual_chall')
  - atp_matches_futures_*.csv     → matches (match_type='futures')
  - atp_matches_amateur.csv       → matches (match_type='amateur')
  - atp_matches_doubles_*.csv     → doubles_matches
  - atp_players.csv               → players
  - atp_rankings_*.csv            → rankings

  WTA:
  - wta_matches_YYYY.csv          → wta_matches (match_type='main')
  - wta_matches_qual_itf_*.csv    → wta_matches (match_type='qual_itf')
  - wta_players.csv               → wta_players
  - wta_rankings_*.csv            → wta_rankings

Usage:
    python dbLoadup.py [--data-dir /path/to/csvs] [--db atp.db]

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
parser.add_argument("--data-dir", default=".", help="Directory containing ATP CSV files")
parser.add_argument("--db", default="atp.db", help="Output SQLite database path")
args = parser.parse_args()

DATA_DIR = "csvData/"
DB_PATH  = args.db

# ── Schema ────────────────────────────────────────────────────────────────────
CREATE_MATCHES_TABLE = """
CREATE TABLE IF NOT EXISTS matches (
    tourney_id         TEXT,
    tourney_name       TEXT,
    surface            TEXT,
    draw_size          INTEGER,
    tourney_level      TEXT,
    tourney_date       TEXT,
    match_num          INTEGER,
    winner_id          INTEGER,
    winner_seed        TEXT,
    winner_entry       TEXT,
    winner_name        TEXT,
    winner_hand        TEXT,
    winner_ht          REAL,
    winner_ioc         TEXT,
    winner_age         REAL,
    loser_id           INTEGER,
    loser_seed         TEXT,
    loser_entry        TEXT,
    loser_name         TEXT,
    loser_hand         TEXT,
    loser_ht           REAL,
    loser_ioc          TEXT,
    loser_age          REAL,
    score              TEXT,
    best_of            INTEGER,
    round              TEXT,
    minutes            REAL,
    w_ace              REAL,
    w_df               REAL,
    w_svpt             REAL,
    w_1stIn            REAL,
    w_1stWon           REAL,
    w_2ndWon           REAL,
    w_SvGms            REAL,
    w_bpSaved          REAL,
    w_bpFaced          REAL,
    l_ace              REAL,
    l_df               REAL,
    l_svpt             REAL,
    l_1stIn            REAL,
    l_1stWon           REAL,
    l_2ndWon           REAL,
    l_SvGms            REAL,
    l_bpSaved          REAL,
    l_bpFaced          REAL,
    winner_rank        REAL,
    winner_rank_points REAL,
    loser_rank         REAL,
    loser_rank_points  REAL,
    match_type         TEXT
);
"""

CREATE_DOUBLES_TABLE = """
CREATE TABLE IF NOT EXISTS doubles_matches (
    tourney_id           TEXT,
    tourney_name         TEXT,
    surface              TEXT,
    draw_size            INTEGER,
    tourney_level        TEXT,
    tourney_date         TEXT,
    match_num            INTEGER,
    winner1_id           INTEGER,
    winner2_id           INTEGER,
    winner_seed          TEXT,
    winner_entry         TEXT,
    loser1_id            INTEGER,
    loser2_id            INTEGER,
    loser_seed           TEXT,
    loser_entry          TEXT,
    score                TEXT,
    best_of              INTEGER,
    round                TEXT,
    winner1_name         TEXT,
    winner1_hand         TEXT,
    winner1_ht           REAL,
    winner1_ioc          TEXT,
    winner1_age          REAL,
    winner2_name         TEXT,
    winner2_hand         TEXT,
    winner2_ht           REAL,
    winner2_ioc          TEXT,
    winner2_age          REAL,
    loser1_name          TEXT,
    loser1_hand          TEXT,
    loser1_ht            REAL,
    loser1_ioc           TEXT,
    loser1_age           REAL,
    loser2_name          TEXT,
    loser2_hand          TEXT,
    loser2_ht            REAL,
    loser2_ioc           TEXT,
    loser2_age           REAL,
    winner1_rank         REAL,
    winner1_rank_points  REAL,
    winner2_rank         REAL,
    winner2_rank_points  REAL,
    loser1_rank          REAL,
    loser1_rank_points   REAL,
    loser2_rank          REAL,
    loser2_rank_points   REAL,
    minutes              REAL,
    w_ace                REAL,
    w_df                 REAL,
    w_svpt               REAL,
    w_1stIn              REAL,
    w_1stWon             REAL,
    w_2ndWon             REAL,
    w_SvGms              REAL,
    w_bpSaved            REAL,
    w_bpFaced            REAL,
    l_ace                REAL,
    l_df                 REAL,
    l_svpt               REAL,
    l_1stIn              REAL,
    l_1stWon             REAL,
    l_2ndWon             REAL,
    l_SvGms              REAL,
    l_bpSaved            REAL,
    l_bpFaced            REAL
);
"""

CREATE_PLAYERS_TABLE = """
CREATE TABLE IF NOT EXISTS players (
    player_id   INTEGER PRIMARY KEY,
    name_first  TEXT,
    name_last   TEXT,
    hand        TEXT,
    dob         TEXT,
    ioc         TEXT,
    height      REAL,
    wikidata_id TEXT
);
"""

CREATE_RANKINGS_TABLE = """
CREATE TABLE IF NOT EXISTS rankings (
    ranking_date TEXT,
    rank         INTEGER,
    player_id    INTEGER,
    points       REAL
);
"""

CREATE_WTA_MATCHES_TABLE = """
CREATE TABLE IF NOT EXISTS wta_matches (
    tourney_id         TEXT,
    tourney_name       TEXT,
    surface            TEXT,
    draw_size          INTEGER,
    tourney_level      TEXT,
    tourney_date       TEXT,
    match_num          INTEGER,
    winner_id          INTEGER,
    winner_seed        TEXT,
    winner_entry       TEXT,
    winner_name        TEXT,
    winner_hand        TEXT,
    winner_ht          REAL,
    winner_ioc         TEXT,
    winner_age         REAL,
    loser_id           INTEGER,
    loser_seed         TEXT,
    loser_entry        TEXT,
    loser_name         TEXT,
    loser_hand         TEXT,
    loser_ht           REAL,
    loser_ioc          TEXT,
    loser_age          REAL,
    score              TEXT,
    best_of            INTEGER,
    round              TEXT,
    minutes            REAL,
    w_ace              REAL,
    w_df               REAL,
    w_svpt             REAL,
    w_1stIn            REAL,
    w_1stWon           REAL,
    w_2ndWon           REAL,
    w_SvGms            REAL,
    w_bpSaved          REAL,
    w_bpFaced          REAL,
    l_ace              REAL,
    l_df               REAL,
    l_svpt             REAL,
    l_1stIn            REAL,
    l_1stWon           REAL,
    l_2ndWon           REAL,
    l_SvGms            REAL,
    l_bpSaved          REAL,
    l_bpFaced          REAL,
    winner_rank        REAL,
    winner_rank_points REAL,
    loser_rank         REAL,
    loser_rank_points  REAL,
    match_type         TEXT
);
"""

CREATE_WTA_PLAYERS_TABLE = """
CREATE TABLE IF NOT EXISTS wta_players (
    player_id   INTEGER PRIMARY KEY,
    name_first  TEXT,
    name_last   TEXT,
    hand        TEXT,
    dob         TEXT,
    ioc         TEXT,
    height      REAL,
    wikidata_id TEXT
);
"""

CREATE_WTA_RANKINGS_TABLE = """
CREATE TABLE IF NOT EXISTS wta_rankings (
    ranking_date TEXT,
    rank         INTEGER,
    player_id    INTEGER,
    points       REAL
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_winner      ON matches(winner_name);
CREATE INDEX IF NOT EXISTS idx_loser       ON matches(loser_name);
CREATE INDEX IF NOT EXISTS idx_surface     ON matches(surface);
CREATE INDEX IF NOT EXISTS idx_date        ON matches(tourney_date);
CREATE INDEX IF NOT EXISTS idx_match_type  ON matches(match_type);
CREATE INDEX IF NOT EXISTS idx_dbl_date    ON doubles_matches(tourney_date);
CREATE INDEX IF NOT EXISTS idx_rankings_player ON rankings(player_id);
CREATE INDEX IF NOT EXISTS idx_rankings_date   ON rankings(ranking_date);
CREATE INDEX IF NOT EXISTS idx_wta_winner      ON wta_matches(winner_name);
CREATE INDEX IF NOT EXISTS idx_wta_loser       ON wta_matches(loser_name);
CREATE INDEX IF NOT EXISTS idx_wta_surface     ON wta_matches(surface);
CREATE INDEX IF NOT EXISTS idx_wta_date        ON wta_matches(tourney_date);
CREATE INDEX IF NOT EXISTS idx_wta_match_type  ON wta_matches(match_type);
CREATE INDEX IF NOT EXISTS idx_wta_rankings_player ON wta_rankings(player_id);
CREATE INDEX IF NOT EXISTS idx_wta_rankings_date   ON wta_rankings(ranking_date);
"""

CREATE_STATS_VIEW = """
CREATE VIEW IF NOT EXISTS player_surface_stats AS
WITH all_serve AS (
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
    AVG(CAST(first_won + second_won AS REAL) / NULLIF(svpt, 0))      AS serve_win_pct,
    AVG(CAST(first_in AS REAL) / NULLIF(svpt, 0))                    AS first_serve_pct,
    AVG(CAST(first_won AS REAL) / NULLIF(first_in, 0))               AS first_serve_win_pct,
    AVG(CAST(second_won AS REAL) / NULLIF(svpt - first_in, 0))       AS second_serve_win_pct,
    AVG(CAST(ace AS REAL) / NULLIF(svpt, 0))                         AS ace_rate,
    AVG(CAST(df AS REAL) / NULLIF(svpt, 0))                          AS df_rate,
    AVG(CAST(bp_saved AS REAL) / NULLIF(bp_faced, 0))                AS bp_save_pct
FROM all_serve
GROUP BY player, surface
HAVING match_count >= 5;
"""

CREATE_WTA_STATS_VIEW = """
CREATE VIEW IF NOT EXISTS wta_player_surface_stats AS
WITH all_serve AS (
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
    FROM wta_matches
    WHERE w_svpt > 0 AND w_1stIn IS NOT NULL

    UNION ALL

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
    FROM wta_matches
    WHERE l_svpt > 0 AND l_1stIn IS NOT NULL
)
SELECT
    player,
    surface,
    COUNT(*)                                                          AS match_count,
    AVG(CAST(first_won + second_won AS REAL) / NULLIF(svpt, 0))      AS serve_win_pct,
    AVG(CAST(first_in AS REAL) / NULLIF(svpt, 0))                    AS first_serve_pct,
    AVG(CAST(first_won AS REAL) / NULLIF(first_in, 0))               AS first_serve_win_pct,
    AVG(CAST(second_won AS REAL) / NULLIF(svpt - first_in, 0))       AS second_serve_win_pct,
    AVG(CAST(ace AS REAL) / NULLIF(svpt, 0))                         AS ace_rate,
    AVG(CAST(df AS REAL) / NULLIF(svpt, 0))                          AS df_rate,
    AVG(CAST(bp_saved AS REAL) / NULLIF(bp_faced, 0))                AS bp_save_pct
FROM all_serve
GROUP BY player, surface
HAVING match_count >= 5;
"""

# ── Column definitions ────────────────────────────────────────────────────────
SINGLES_COLS = [
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
SINGLES_EXPECTED = set(SINGLES_COLS)

DOUBLES_COLS = [
    "tourney_id","tourney_name","surface","draw_size","tourney_level",
    "tourney_date","match_num","winner1_id","winner2_id","winner_seed",
    "winner_entry","loser1_id","loser2_id","loser_seed","loser_entry",
    "score","best_of","round",
    "winner1_name","winner1_hand","winner1_ht","winner1_ioc","winner1_age",
    "winner2_name","winner2_hand","winner2_ht","winner2_ioc","winner2_age",
    "loser1_name","loser1_hand","loser1_ht","loser1_ioc","loser1_age",
    "loser2_name","loser2_hand","loser2_ht","loser2_ioc","loser2_age",
    "winner1_rank","winner1_rank_points","winner2_rank","winner2_rank_points",
    "loser1_rank","loser1_rank_points","loser2_rank","loser2_rank_points",
    "minutes","w_ace","w_df","w_svpt","w_1stIn","w_1stWon","w_2ndWon",
    "w_SvGms","w_bpSaved","w_bpFaced","l_ace","l_df","l_svpt","l_1stIn",
    "l_1stWon","l_2ndWon","l_SvGms","l_bpSaved","l_bpFaced",
]
DOUBLES_EXPECTED = set(DOUBLES_COLS)

PLAYERS_COLS = ["player_id","name_first","name_last","hand","dob","ioc","height","wikidata_id"]
RANKINGS_COLS = ["ranking_date","rank","player","points"]

# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(val):
    if val is None:
        return None
    v = val.strip()
    return None if v == "" else v

def load_singles_csv(conn, path, match_type, table="matches"):
    rows_inserted = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        if not SINGLES_EXPECTED.issubset(cols):
            missing = SINGLES_EXPECTED - cols
            print(f"  ⚠  Skipping {os.path.basename(path)} — missing cols: {missing}")
            return 0

        all_cols = SINGLES_COLS + ["match_type"]
        placeholder = ",".join(["?"] * len(all_cols))
        sql = f"INSERT INTO {table} ({','.join(all_cols)}) VALUES ({placeholder})"

        batch = []
        for row in reader:
            batch.append(tuple(safe(row.get(c, "")) for c in SINGLES_COLS) + (match_type,))
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                rows_inserted += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows_inserted += len(batch)
    return rows_inserted

def load_doubles_csv(conn, path):
    rows_inserted = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        if not DOUBLES_EXPECTED.issubset(cols):
            missing = DOUBLES_EXPECTED - cols
            print(f"  ⚠  Skipping {os.path.basename(path)} — missing cols: {missing}")
            return 0

        placeholder = ",".join(["?"] * len(DOUBLES_COLS))
        sql = f"INSERT INTO doubles_matches ({','.join(DOUBLES_COLS)}) VALUES ({placeholder})"

        batch = []
        for row in reader:
            batch.append(tuple(safe(row.get(c, "")) for c in DOUBLES_COLS))
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                rows_inserted += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows_inserted += len(batch)
    return rows_inserted

def load_players_csv(conn, path, table="players"):
    rows_inserted = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        placeholder = ",".join(["?"] * len(PLAYERS_COLS))
        sql = f"INSERT OR REPLACE INTO {table} ({','.join(PLAYERS_COLS)}) VALUES ({placeholder})"
        batch = []
        for row in reader:
            batch.append(tuple(safe(row.get(c, "")) for c in PLAYERS_COLS))
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                rows_inserted += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            rows_inserted += len(batch)
    return rows_inserted

def load_rankings_csv(conn, path, table="rankings"):
    rows_inserted = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        # rankings files use "player" as the column name for player_id
        db_cols = ["ranking_date", "rank", "player_id", "points"]
        placeholder = ",".join(["?"] * len(db_cols))
        sql = f"INSERT INTO {table} ({','.join(db_cols)}) VALUES ({placeholder})"
        batch = []
        for row in reader:
            batch.append((
                safe(row.get("ranking_date", "")),
                safe(row.get("rank", "")),
                safe(row.get("player", "")),
                safe(row.get("points", "")),
            ))
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
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    # Drop everything so a re-run always produces a clean, up-to-date schema.
    conn.execute("DROP VIEW  IF EXISTS player_surface_stats;")
    conn.execute("DROP VIEW  IF EXISTS wta_player_surface_stats;")
    conn.execute("DROP TABLE IF EXISTS matches;")
    conn.execute("DROP TABLE IF EXISTS doubles_matches;")
    conn.execute("DROP TABLE IF EXISTS players;")
    conn.execute("DROP TABLE IF EXISTS rankings;")
    conn.execute("DROP TABLE IF EXISTS wta_matches;")
    conn.execute("DROP TABLE IF EXISTS wta_players;")
    conn.execute("DROP TABLE IF EXISTS wta_rankings;")
    conn.commit()

    conn.execute(CREATE_MATCHES_TABLE)
    conn.execute(CREATE_DOUBLES_TABLE)
    conn.execute(CREATE_PLAYERS_TABLE)
    conn.execute(CREATE_RANKINGS_TABLE)
    conn.execute(CREATE_WTA_MATCHES_TABLE)
    conn.execute(CREATE_WTA_PLAYERS_TABLE)
    conn.execute(CREATE_WTA_RANKINGS_TABLE)
    conn.commit()

    total = 0
    t0 = time.time()

    # ── Main singles (1968-2024) ──────────────────────────────────────────────
    main_files = sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_[0-9][0-9][0-9][0-9].csv")))
    if not main_files:
        print(f"No atp_matches_YYYY.csv files found in '{DATA_DIR}'")
        sys.exit(1)
    print(f"\nLoading {len(main_files)} main singles files…")
    for path in main_files:
        label = os.path.basename(path).replace("atp_matches_","").replace(".csv","")
        n = load_singles_csv(conn, path, "main")
        conn.commit()
        total += n
        print(f"  {label}: {n:,} rows")

    # ── Qualifying / Challenger ───────────────────────────────────────────────
    qc_files = sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_qual_chall_*.csv")))
    print(f"\nLoading {len(qc_files)} qual/challenger files…")
    qc_total = 0
    for path in qc_files:
        label = os.path.basename(path).replace("atp_matches_qual_chall_","").replace(".csv","")
        n = load_singles_csv(conn, path, "qual_chall")
        conn.commit()
        qc_total += n
        print(f"  {label}: {n:,} rows")
    total += qc_total
    print(f"  → {qc_total:,} qual/challenger rows total")

    # ── Futures ───────────────────────────────────────────────────────────────
    fut_files = sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_futures_*.csv")))
    print(f"\nLoading {len(fut_files)} futures files…")
    fut_total = 0
    for path in fut_files:
        label = os.path.basename(path).replace("atp_matches_futures_","").replace(".csv","")
        n = load_singles_csv(conn, path, "futures")
        conn.commit()
        fut_total += n
        print(f"  {label}: {n:,} rows")
    total += fut_total
    print(f"  → {fut_total:,} futures rows total")

    # ── Amateur ───────────────────────────────────────────────────────────────
    amateur_path = os.path.join(DATA_DIR, "atp_matches_amateur.csv")
    if os.path.exists(amateur_path):
        print(f"\nLoading amateur file…")
        n = load_singles_csv(conn, amateur_path, "amateur")
        conn.commit()
        total += n
        print(f"  amateur: {n:,} rows")

    # ── Doubles ───────────────────────────────────────────────────────────────
    dbl_files = sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_doubles_*.csv")))
    print(f"\nLoading {len(dbl_files)} doubles files…")
    dbl_total = 0
    for path in dbl_files:
        label = os.path.basename(path).replace("atp_matches_doubles_","").replace(".csv","")
        n = load_doubles_csv(conn, path)
        conn.commit()
        dbl_total += n
        print(f"  {label}: {n:,} rows")
    print(f"  → {dbl_total:,} doubles rows total")

    # ── Players ───────────────────────────────────────────────────────────────
    players_path = os.path.join(DATA_DIR, "atp_players.csv")
    if os.path.exists(players_path):
        print(f"\nLoading players…")
        n = load_players_csv(conn, players_path)
        conn.commit()
        print(f"  players: {n:,} rows")

    # ── Rankings ──────────────────────────────────────────────────────────────
    rankings_files = sorted(glob.glob(os.path.join(DATA_DIR, "atp_rankings_*.csv")))
    print(f"\nLoading {len(rankings_files)} rankings files…")
    rank_total = 0
    for path in rankings_files:
        label = os.path.basename(path).replace("atp_rankings_","").replace(".csv","")
        n = load_rankings_csv(conn, path)
        conn.commit()
        rank_total += n
        print(f"  {label}: {n:,} rows")
    print(f"  → {rank_total:,} rankings rows total")

    # ── WTA Main singles ──────────────────────────────────────────────────────
    wta_main_files = sorted(glob.glob(os.path.join(DATA_DIR, "wta_matches_[0-9][0-9][0-9][0-9].csv")))
    print(f"\nLoading {len(wta_main_files)} WTA main singles files…")
    wta_total = 0
    for path in wta_main_files:
        label = os.path.basename(path).replace("wta_matches_","").replace(".csv","")
        n = load_singles_csv(conn, path, "main", table="wta_matches")
        conn.commit()
        wta_total += n
        print(f"  {label}: {n:,} rows")
    print(f"  → {wta_total:,} WTA main rows total")
    total += wta_total

    # ── WTA Qualifying / ITF ──────────────────────────────────────────────────
    wta_qi_files = sorted(glob.glob(os.path.join(DATA_DIR, "wta_matches_qual_itf_*.csv")))
    print(f"\nLoading {len(wta_qi_files)} WTA qual/ITF files…")
    wta_qi_total = 0
    for path in wta_qi_files:
        label = os.path.basename(path).replace("wta_matches_qual_itf_","").replace(".csv","")
        n = load_singles_csv(conn, path, "qual_itf", table="wta_matches")
        conn.commit()
        wta_qi_total += n
        print(f"  {label}: {n:,} rows")
    total += wta_qi_total
    print(f"  → {wta_qi_total:,} WTA qual/ITF rows total")

    # ── WTA Players ───────────────────────────────────────────────────────────
    wta_players_path = os.path.join(DATA_DIR, "wta_players.csv")
    if os.path.exists(wta_players_path):
        print(f"\nLoading WTA players…")
        n = load_players_csv(conn, wta_players_path, table="wta_players")
        conn.commit()
        print(f"  wta_players: {n:,} rows")

    # ── WTA Rankings ──────────────────────────────────────────────────────────
    wta_rankings_files = sorted(glob.glob(os.path.join(DATA_DIR, "wta_rankings_*.csv")))
    print(f"\nLoading {len(wta_rankings_files)} WTA rankings files…")
    wta_rank_total = 0
    for path in wta_rankings_files:
        label = os.path.basename(path).replace("wta_rankings_","").replace(".csv","")
        n = load_rankings_csv(conn, path, table="wta_rankings")
        conn.commit()
        wta_rank_total += n
        print(f"  {label}: {n:,} rows")
    print(f"  → {wta_rank_total:,} WTA rankings rows total")

    # ── Indexes & view ────────────────────────────────────────────────────────
    print(f"\nCreating indexes…")
    for stmt in CREATE_INDEXES.strip().split("\n"):
        if stmt.strip():
            conn.execute(stmt)

    print("Creating player_surface_stats view…")
    conn.execute(CREATE_STATS_VIEW)
    print("Creating wta_player_surface_stats view…")
    conn.execute(CREATE_WTA_STATS_VIEW)
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    print(f"\n✓ Done — {total:,} singles matches loaded in {elapsed:.1f}s")
    print(f"  Database: {DB_PATH}")
    print(f"\nNext step:\n  python3 simulator.py")

if __name__ == "__main__":
    main()
