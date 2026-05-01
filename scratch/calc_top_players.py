import sqlite3
import string

def get_top_players():
    conn = sqlite3.connect('tennis.db')
    conn.row_factory = sqlite3.Row
    
    # We'll use the player_surface_stats table since it has match_count which we use for 'rank'
    # and it filters to players that actually have data.
    
    alphabet = string.ascii_lowercase
    pairs = [a + b for a in alphabet for b in alphabet]
    
    top_players = set()
    
    for pair in pairs:
        # Search for players starting with this pair
        cur = conn.execute("""
            SELECT p.name, p.gender, SUM(pss.match_count) as total_matches
            FROM Player p
            JOIN player_surface_stats pss ON p.id = pss.player_id
            WHERE LOWER(p.name) LIKE ?
            GROUP BY p.id
            ORDER BY total_matches DESC
            LIMIT 6
        """, (pair + '%',))
        
        for row in cur:
            top_players.add((row['name'], row['gender']))
            
    conn.close()
    return sorted(list(top_players))

if __name__ == "__main__":
    players = get_top_players()
    print(f"Total unique players in 'Top 2-letter' set: {len(players)}")
    # Print first few as example
    for p in players[:10]:
        print(p)
