"""
Tennis Match Database Setup
============================
Loads ATP and WTA CSV files into the normalised SQLite schema:

  Players      → Player  (gender='M' for ATP, 'F' for WTA)
  Tournaments  → Tournament
  Singles      → Match + PlayerMatchStats + PlayerTournamentStats
  Rankings     → derived view, no load needed

Usage:
    python dbLoadup.py [--data-dir ./csvData] [--db tennis.db]
"""

import sqlite3
import csv
import glob
import math
import os
import sys
import argparse
import time

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", default="csvData/")
parser.add_argument("--db",       default="tennis.db")
args = parser.parse_args()

DATA_DIR = args.data_dir
DB_PATH  = args.db

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS Tournament (
    id        TEXT PRIMARY KEY,
    name      TEXT,
    surface   TEXT,
    draw_size INTEGER,
    level     TEXT,
    date      TEXT
);

CREATE TABLE IF NOT EXISTS Player (
    id      INTEGER PRIMARY KEY,
    name    TEXT,
    hand    TEXT,
    height  REAL,
    ioc     TEXT,
    dob     TEXT,
    gender  TEXT
);

CREATE TABLE IF NOT EXISTS Match (
    tournament_id  TEXT,
    match_num      INTEGER,
    winner_id      INTEGER,
    winner_age     REAL,
    loser_id       INTEGER,
    loser_age      REAL,
    score          TEXT,
    best_of        INTEGER,
    round          TEXT,
    minutes        REAL,
    match_type     TEXT,
    PRIMARY KEY (tournament_id, match_num),
    FOREIGN KEY (tournament_id) REFERENCES Tournament(id),
    FOREIGN KEY (winner_id)     REFERENCES Player(id),
    FOREIGN KEY (loser_id)      REFERENCES Player(id)
);

CREATE TABLE IF NOT EXISTS PlayerMatchStats (
    tournament_id  TEXT,
    match_num      INTEGER,
    player_id      INTEGER,
    ace            REAL,
    df             REAL,
    svpt           REAL,
    first_in       REAL,
    first_won      REAL,
    second_won     REAL,
    sv_gms         REAL,
    bp_saved       REAL,
    bp_faced       REAL,
    PRIMARY KEY (tournament_id, match_num, player_id),
    FOREIGN KEY (tournament_id, match_num) REFERENCES Match(tournament_id, match_num),
    FOREIGN KEY (player_id) REFERENCES Player(id)
);

CREATE TABLE IF NOT EXISTS PlayerTournamentStats (
    tournament_id  TEXT,
    player_id      INTEGER,
    seed           TEXT,
    entry          TEXT,
    rank           REAL,
    rank_points    REAL,
    PRIMARY KEY (tournament_id, player_id),
    FOREIGN KEY (tournament_id) REFERENCES Tournament(id),
    FOREIGN KEY (player_id) REFERENCES Player(id)
);
"""

VIEWS = """
CREATE VIEW IF NOT EXISTS Rankings AS
SELECT
    t.date          AS ranking_date,
    pts.rank        AS rank,
    pts.player_id   AS player_id,
    pts.rank_points AS points,
    p.gender
FROM PlayerTournamentStats pts
JOIN Tournament t ON t.id = pts.tournament_id
JOIN Player     p ON p.id = pts.player_id
WHERE pts.rank IS NOT NULL;
"""

# player_surface_stats is a TABLE, not a view, so we can store
# pre-computed exponentially-weighted averages.
SURFACE_STATS_TABLE = """
CREATE TABLE IF NOT EXISTS player_surface_stats (
    player_id            INTEGER,
    player               TEXT,
    surface              TEXT,
    match_count          INTEGER,
    serve_win_pct        REAL,
    first_serve_pct      REAL,
    first_serve_win_pct  REAL,
    second_serve_win_pct REAL,
    ace_rate             REAL,
    df_rate              REAL,
    bp_save_pct          REAL,
    PRIMARY KEY (player_id, surface)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_match_winner    ON Match(winner_id);
CREATE INDEX IF NOT EXISTS idx_match_loser     ON Match(loser_id);
CREATE INDEX IF NOT EXISTS idx_match_type      ON Match(match_type);
CREATE INDEX IF NOT EXISTS idx_match_tourney   ON Match(tournament_id);
CREATE INDEX IF NOT EXISTS idx_pms_player      ON PlayerMatchStats(player_id);
CREATE INDEX IF NOT EXISTS idx_pts_player      ON PlayerTournamentStats(player_id);
CREATE INDEX IF NOT EXISTS idx_pts_rank        ON PlayerTournamentStats(rank);
CREATE INDEX IF NOT EXISTS idx_tourney_date    ON Tournament(date);
CREATE INDEX IF NOT EXISTS idx_tourney_surface ON Tournament(surface);
CREATE INDEX IF NOT EXISTS idx_player_gender   ON Player(gender);
"""

# ── Helpers ───────────────────────────────────────────────────────────────────
def safe(val):
    """Strip whitespace; return None for empty strings."""
    if val is None:
        return None
    v = str(val).strip()
    return None if v == "" else v


def compute_age(dob: str, tourney_date: str):
    """
    Return age in decimal years from two YYYYMMDD strings.
    Returns None if either value is missing or malformed.
    """
    try:
        d = date_type(int(dob[:4]),         int(dob[4:6]),         int(dob[6:8]))
        t = date_type(int(tourney_date[:4]), int(tourney_date[4:6]), int(tourney_date[6:8]))
        return round((t - d).days / 365.25, 4)
    except Exception:
        return None


def resolve_age(csv_age, player_dob, tourney_date):
    """
    Priority:
      1. Age from CSV if present and valid
      2. Computed from player dob + tourney_date
      3. None  (stored as NULL; API reports 'Unknown')
    """
    if csv_age is not None:
        try:
            return float(csv_age)
        except (ValueError, TypeError):
            pass
    if player_dob and tourney_date:
        return compute_age(player_dob, tourney_date)
    return None


# ── Player dob cache (avoid re-querying DB for every match row) ───────────────
_player_dob_cache: dict[int, str | None] = {}

def get_player_dob(conn, player_id: int) -> str | None:
    if player_id in _player_dob_cache:
        return _player_dob_cache[player_id]
    cur = conn.execute("SELECT dob FROM Player WHERE id = ?", (player_id,))
    row = cur.fetchone()
    dob = row["dob"] if row else None
    _player_dob_cache[player_id] = dob
    return dob


# ── Loaders ───────────────────────────────────────────────────────────────────
def load_players(conn, path: str, gender: str) -> int:
    """Load a players CSV file into Player, joining first + last name."""
    n = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            first = safe(row.get("name_first")) or ""
            last  = safe(row.get("name_last"))  or ""
            name  = (first + " " + last).strip() or None
            conn.execute("""
                INSERT OR IGNORE INTO Player (id, name, hand, height, ioc, dob, gender)
                VALUES (?,?,?,?,?,?,?)
            """, (
                safe(row.get("player_id")),
                name,
                safe(row.get("hand")),
                safe(row.get("height")),
                safe(row.get("ioc")),
                safe(row.get("dob")),
                gender,
            ))
            n += 1
    return n


REQUIRED_SINGLES_COLS = {
    "tourney_id", "tourney_name", "surface", "draw_size", "tourney_level",
    "tourney_date", "match_num", "winner_id", "winner_age", "loser_id",
    "loser_age", "score", "best_of", "round",
}


def load_singles(conn, path: str, match_type: str) -> dict:
    """
    Load one singles CSV file into Tournament, Match,
    PlayerMatchStats, and PlayerTournamentStats.
    Returns a counts dict.
    """
    counts = {"tournaments": 0, "matches": 0, "stats": 0, "pts": 0}
    tournaments_seen: set[str]   = set()
    pts_seen:         set[tuple] = set()   # (tournament_id, player_id)

    match_batch = []
    stats_batch = []
    pts_batch   = []

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader     = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])

        if not REQUIRED_SINGLES_COLS.issubset(fieldnames):
            missing = REQUIRED_SINGLES_COLS - fieldnames
            print(f"    ⚠  Skipping {os.path.basename(path)} — missing cols: {missing}")
            return counts

        has_stats = "w_svpt" in fieldnames

        for row in reader:
            tid   = safe(row["tourney_id"])
            tdate = safe(row["tourney_date"])
            mnum  = safe(row["match_num"])
            wid   = safe(row["winner_id"])
            lid   = safe(row["loser_id"])
            if not all([tid, mnum, wid, lid]):
                continue

            # ── Tournament (insert once per unique id) ────────────────────────
            if tid not in tournaments_seen:
                conn.execute("""
                    INSERT OR IGNORE INTO Tournament (id, name, surface, draw_size, level, date)
                    VALUES (?,?,?,?,?,?)
                """, (
                    tid,
                    safe(row.get("tourney_name")),
                    safe(row.get("surface")),
                    safe(row.get("draw_size")),
                    safe(row.get("tourney_level")),
                    tdate,
                ))
                tournaments_seen.add(tid)
                counts["tournaments"] += 1

            # ── Age resolution ────────────────────────────────────────────────
            w_dob = get_player_dob(conn, int(wid))
            l_dob = get_player_dob(conn, int(lid))
            w_age = resolve_age(safe(row.get("winner_age")), w_dob, tdate)
            l_age = resolve_age(safe(row.get("loser_age")),  l_dob, tdate)

            # ── Match ─────────────────────────────────────────────────────────
            match_batch.append((
                tid, mnum, wid, w_age, lid, l_age,
                safe(row.get("score")),
                safe(row.get("best_of")),
                safe(row.get("round")),
                safe(row.get("minutes")),
                match_type,
            ))
            counts["matches"] += 1

            # ── PlayerMatchStats — one row per player ─────────────────────────
            if has_stats:
                def stat_row(pid, prefix):
                    return (
                        tid, mnum, pid,
                        safe(row.get(f"{prefix}ace")),
                        safe(row.get(f"{prefix}df")),
                        safe(row.get(f"{prefix}svpt")),
                        safe(row.get(f"{prefix}1stIn")),
                        safe(row.get(f"{prefix}1stWon")),
                        safe(row.get(f"{prefix}2ndWon")),
                        safe(row.get(f"{prefix}SvGms")),
                        safe(row.get(f"{prefix}bpSaved")),
                        safe(row.get(f"{prefix}bpFaced")),
                    )
                stats_batch.append(stat_row(wid, "w_"))
                stats_batch.append(stat_row(lid, "l_"))
                counts["stats"] += 2

            # ── PlayerTournamentStats — one row per player per tournament ─────
            for pid, seed_k, entry_k, rank_k, pts_k in [
                (wid, "winner_seed", "winner_entry", "winner_rank", "winner_rank_points"),
                (lid, "loser_seed",  "loser_entry",  "loser_rank",  "loser_rank_points"),
            ]:
                key = (tid, pid)
                if key not in pts_seen:
                    pts_batch.append((
                        tid, pid,
                        safe(row.get(seed_k)),
                        safe(row.get(entry_k)),
                        safe(row.get(rank_k)),
                        safe(row.get(pts_k)),
                    ))
                    pts_seen.add(key)
                    counts["pts"] += 1

            # Flush to DB every 2 000 match rows to keep memory manageable
            if len(match_batch) >= 2000:
                _flush(conn, match_batch, stats_batch, pts_batch)
                match_batch, stats_batch, pts_batch = [], [], []

        # Final flush for remainder
        _flush(conn, match_batch, stats_batch, pts_batch)

    return counts


def _flush(conn, matches, stats, pts):
    if matches:
        conn.executemany("""
            INSERT OR IGNORE INTO Match
                (tournament_id, match_num, winner_id, winner_age,
                 loser_id, loser_age, score, best_of, round, minutes, match_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, matches)
    if stats:
        conn.executemany("""
            INSERT OR IGNORE INTO PlayerMatchStats
                (tournament_id, match_num, player_id,
                 ace, df, svpt, first_in, first_won, second_won,
                 sv_gms, bp_saved, bp_faced)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, stats)
    if pts:
        conn.executemany("""
            INSERT OR IGNORE INTO PlayerTournamentStats
                (tournament_id, player_id, seed, entry, rank, rank_points)
            VALUES (?,?,?,?,?,?)
        """, pts)


# ── Weighted surface stats ─────────────────────────────────────────────────────
# Exponential decay: matches from HALF_LIFE_DAYS ago count half as much
# as a match played today. ~730 days ≈ 2 years feels right for tennis —
# recent form matters but a clay-court baseline built over many years
# shouldn't evaporate after one bad season.
HALF_LIFE_DAYS = 730
DECAY_LAMBDA   = math.log(2) / HALF_LIFE_DAYS   # ≈ 0.00095




def compute_weighted_surface_stats(conn, min_matches: int = 40):
    """
    Populate player_surface_stats using a single SQL INSERT … SELECT.

    The core technique is exponential-decay weighting via SQLite's EXP() and
    julianday() functions (available since SQLite 3.35, released March 2021).

    For each player × surface bucket we compute:

        weighted_avg(stat) = SUM(w_i * numerator_i) / SUM(w_i * denominator_i)

    where  w_i = EXP(-λ * days_since_match).

    Stats whose denominator can be zero on a given row (first_serve_win_pct
    needs first_in > 0; bp_save_pct needs bp_faced > 0) use CASE guards so
    those rows are silently excluded from just that ratio without being thrown
    out of the bucket entirely.
    """
    print("  Computing exponentially-weighted surface stats (SQL)…")

    lam = DECAY_LAMBDA   # embed as a plain float in the SQL string

    conn.execute("DELETE FROM player_surface_stats;")
    conn.execute(f"""
        INSERT INTO player_surface_stats
            (player_id, player, surface, match_count,
             serve_win_pct, first_serve_pct, first_serve_win_pct,
             second_serve_win_pct, ace_rate, df_rate, bp_save_pct)

        -- ── CTE: attach a per-row exponential decay weight ──────────────────
        WITH weighted AS (
            SELECT
                pms.player_id,
                p.name    AS player,
                t.surface,

                -- Convert YYYYMMDD → ISO date, then measure days from today.
                -- EXP() and julianday() require SQLite >= 3.35 (March 2021).
                EXP(
                    -{lam} * (
                        julianday('now') -
                        julianday(
                            substr(t.date, 1, 4) || '-' ||
                            substr(t.date, 5, 2) || '-' ||
                            substr(t.date, 7, 2)
                        )
                    )
                )         AS w,

                pms.svpt,
                pms.first_in,
                pms.first_won,
                pms.second_won,
                pms.ace,
                pms.df,
                pms.bp_saved,
                pms.bp_faced

            FROM PlayerMatchStats pms
            JOIN Match      m ON m.tournament_id = pms.tournament_id
                             AND m.match_num     = pms.match_num
            JOIN Tournament t ON t.id            = m.tournament_id
            JOIN Player     p ON p.id            = pms.player_id
            WHERE pms.svpt    >  0
              AND pms.first_in IS NOT NULL
        )
        SELECT
            player_id,
            player,
            surface,
            COUNT(*)                                                AS match_count,

            -- serve_win_pct: weighted (first_won + second_won) / svpt
            SUM(w * (first_won + second_won))
                / NULLIF(SUM(w * svpt), 0)                         AS serve_win_pct,

            -- first_serve_pct: weighted first_in / svpt
            SUM(w * first_in)
                / NULLIF(SUM(w * svpt), 0)                         AS first_serve_pct,

            -- first_serve_win_pct: only rows where first_in > 0 contribute
            SUM(CASE WHEN first_in > 0 THEN w * first_won  ELSE 0 END)
                / NULLIF(
                    SUM(CASE WHEN first_in > 0 THEN w * first_in ELSE 0 END),
                  0)                                                AS first_serve_win_pct,

            -- second_serve_win_pct: denominator is (svpt - first_in)
            SUM(CASE WHEN svpt - first_in > 0 THEN w * second_won        ELSE 0 END)
                / NULLIF(
                    SUM(CASE WHEN svpt - first_in > 0
                             THEN w * (svpt - first_in) ELSE 0 END),
                  0)                                                AS second_serve_win_pct,

            -- ace_rate: weighted ace / svpt
            SUM(w * ace)  / NULLIF(SUM(w * svpt), 0)               AS ace_rate,

            -- df_rate: weighted df / svpt
            SUM(w * df)   / NULLIF(SUM(w * svpt), 0)               AS df_rate,

            -- bp_save_pct: only rows where bp_faced > 0 contribute
            SUM(CASE WHEN bp_faced > 0 THEN w * bp_saved ELSE 0 END)
                / NULLIF(
                    SUM(CASE WHEN bp_faced > 0 THEN w * bp_faced ELSE 0 END),
                  0)                                                AS bp_save_pct

        FROM weighted
        GROUP BY player_id, surface
        HAVING match_count >= {min_matches}
    """)

    cur = conn.execute("SELECT COUNT(*) AS n FROM player_surface_stats")
    print(f"    → {cur.fetchone()['n']:,} (player, surface) combinations written")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not os.path.isdir(DATA_DIR):
        print(f"Data directory not found: {DATA_DIR}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # ── Clean slate ───────────────────────────────────────────────────────────
    for view  in ["Rankings"]:
        conn.execute(f"DROP VIEW  IF EXISTS {view};")
    for table in ["player_surface_stats", "PlayerMatchStats", "PlayerTournamentStats",
                  "Match", "Tournament", "Player"]:
        conn.execute(f"DROP TABLE IF EXISTS {table};")
    conn.commit()

    for stmt in SCHEMA.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.execute(SURFACE_STATS_TABLE)
    conn.commit()

    t0    = time.time()
    total = 0

    # ── Players (must load before matches so dob lookup works) ────────────────
    atp_players = os.path.join(DATA_DIR, "atp_players.csv")
    if os.path.exists(atp_players):
        n = load_players(conn, atp_players, gender="M")
        conn.commit()
        print(f"  ATP players: {n:,}")
    else:
        print(f"  ⚠  atp_players.csv not found in {DATA_DIR}")

    wta_players = os.path.join(DATA_DIR, "wta_players.csv")
    if os.path.exists(wta_players):
        n = load_players(conn, wta_players, gender="F")
        conn.commit()
        print(f"  WTA players: {n:,}")
    else:
        print(f"  ⚠  wta_players.csv not found in {DATA_DIR}")

    # ── Singles file groups ───────────────────────────────────────────────────
    file_groups = [
        (sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_[0-9][0-9][0-9][0-9].csv"))), "main"),
        (sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_qual_chall_*.csv"))),          "qual_chall"),
        (sorted(glob.glob(os.path.join(DATA_DIR, "atp_matches_futures_*.csv"))),             "futures"),
        (sorted(glob.glob(os.path.join(DATA_DIR, "wta_matches_[0-9][0-9][0-9][0-9].csv"))), "main"),
        (sorted(glob.glob(os.path.join(DATA_DIR, "wta_matches_qual_itf_*.csv"))),            "qual_itf"),
    ]
    amateur = os.path.join(DATA_DIR, "atp_matches_amateur.csv")
    if os.path.exists(amateur):
        file_groups.append(([amateur], "amateur"))

    for files, match_type in file_groups:
        if not files:
            continue
        group_total = 0
        print(f"\n  Loading {len(files)} '{match_type}' file(s)…")
        for path in files:
            counts = load_singles(conn, path, match_type)
            conn.commit()
            group_total += counts["matches"]
            print(f"    {os.path.basename(path)}: "
                  f"{counts['matches']:,} matches  "
                  f"{counts['stats']:,} stat rows  "
                  f"{counts['pts']:,} tournament entries")
        total += group_total
        print(f"  → {group_total:,} '{match_type}' matches total")

    # ── Indexes ───────────────────────────────────────────────────────────────
    print("\n  Creating indexes…")
    for stmt in INDEXES.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt)

    # ── Views ─────────────────────────────────────────────────────────────────
    print("  Creating views…")
    for stmt in VIEWS.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.commit()

    # ── Weighted surface stats ─────────────────────────────────────────────────
    compute_weighted_surface_stats(conn, min_matches=60)
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    print(f"\n✓  Done — {total:,} matches in {elapsed:.1f}s → {DB_PATH}")
    print(f"\n  Next step: python server.py --db {DB_PATH}")


if __name__ == "__main__":
    main()