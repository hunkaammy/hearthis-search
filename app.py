from flask import Flask, request, jsonify, render_template
import requests
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from cachetools import TTLCache
from rapidfuzz import fuzz

app = Flask(__name__)

# CONFIGURATION
# ---------------------------------------------------------
SEARCH_CACHE = TTLCache(maxsize=3000, ttl=1800) # Increased Cache to 30 mins

# --- LIMIT BYPASS 1: ROTATING AGENTS ---
# We switch identities to prevent the server from blocking us
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
]

def get_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}

# ---------------------------------------------------------
# MASSIVE ARTIST DATABASE
# ---------------------------------------------------------
KNOWN_ARTISTS = [
    "aidm", "allindiandjsclub", "djnyk", "djo2srk", "djshadowdubai", "desitech", 
    "djshree", "djchetas", "djaqueel", "djlemon", "djparoma",
    "djkawal", "djbteja", "djrix", "djsmita", "djnotorious", 
    "djsyrah", "djsarfaraz", "djasif", "djsubham", "djtejas",
    "djakhil", "djyogi", "djamit", "djruanon", "djkwid",
    "remix", "bollywood", "clubmirchi", "midnight", "bdes",
    "djremix", "mashup", "desiremix", "hindiremix", "punjabiremix",
    "djdalal", "djshouki", "djshaan", "djshadow", "djrink"
]

random.shuffle(KNOWN_ARTISTS)

# ---------------------------------------------------------
# HELPER: Relevance Scorer
# ---------------------------------------------------------
def calculate_score(query, track):
    title = track.get('title', '').lower()
    user = track.get('user', {}).get('username', '').lower()
    combined = f"{title} {user}"
    
    # 1. Exact match bonus
    if query in title: return 100
    
    # 2. Split match (e.g. "Wakhra Swag" in "Swag Wakhra")
    query_parts = query.split()
    if len(query_parts) > 1 and all(part in combined for part in query_parts):
        return 95

    # 3. Fuzzy match
    score = fuzz.token_set_ratio(query, combined)
    
    # 4. Downloadable tracks get a small boost
    if track.get('download_url'): score += 5
    
    return score

# ---------------------------------------------------------
# SEARCH WORKERS (WITH BYPASS LOGIC)
# ---------------------------------------------------------
def fetch_global(query, page=1):
    """
    Worker 1: Hits the main global search API.
    Bypass Upgrade: Accepts 'page' to dig deeper than the default limit.
    """
    try:
        r = requests.get(
            "https://api-v2.hearthis.at/search",
            params={"q": query, "type": "tracks", "page": page, "count": 20}, # API limits count to 20 usually
            headers=get_headers(), timeout=6
        )
        if r.status_code != 200: return []
        
        data = r.json()
        if isinstance(data, list): return data
        return [v for v in data.values() if isinstance(v, dict)] if isinstance(data, dict) else []
    except:
        return []

def fetch_artist(artist, query):
    """
    Worker 2: Hits specific artist API
    Bypass Upgrade: Fetches 100 tracks instead of 40.
    """
    try:
        # Requesting 100 tracks (The unofficial max limit)
        r = requests.get(
            f"https://api-v2.hearthis.at/{artist}/",
            params={"type": "tracks", "count": 100}, 
            headers=get_headers(), timeout=6
        )
        data = r.json()
        
        matches = []
        if isinstance(data, list):
            for t in data:
                if isinstance(t, dict):
                    # Filter: Only keep tracks that score > 45 relevance
                    score = calculate_score(query, t)
                    if score > 45:
                        t['_score'] = score
                        matches.append(t)
        return matches
    except:
        return []

# ---------------------------------------------------------
# FLASK ROUTES
# ---------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search")
def search():
    start_time = time.time()
    q = request.args.get("q", "").strip().lower()
    
    if len(q) < 2: return jsonify([])
    if q in SEARCH_CACHE: return jsonify(SEARCH_CACHE[q])

    print(f"🚀 Limitless Search: {q}")
    
    results = []
    seen_ids = set()

    # --- PARALLEL EXECUTION ---
    # Max Workers = 30 (High speed)
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = []
        
        # 1. Global Search (PAGES 1, 2, 3) - Bypasses the "Page 1 Only" limit
        for i in range(1, 4):
            futures.append(executor.submit(fetch_global, q, i))
        
        # 2. Artist Search (Top 20 artists)
        # We check 20 different artists DEEP (100 tracks each)
        target_artists = KNOWN_ARTISTS[:20] 
        
        for artist in target_artists:
            futures.append(executor.submit(fetch_artist, artist, q))
            
        # 3. Gather Results
        for future in as_completed(futures):
            tracks = future.result()
            for t in tracks:
                tid = t.get('id') # Use ID for deduplication, it's safer
                
                if tid and tid not in seen_ids:
                    # Ensure score exists
                    if '_score' not in t:
                        t['_score'] = calculate_score(q, t)
                    
                    # Filter junk (Score < 45)
                    if t['_score'] > 45:
                        seen_ids.add(tid)
                        results.append(t)

    # Sort: Relevance > Plays
    results.sort(key=lambda x: (x.get('_score', 0), int(x.get('playback_count', 0))), reverse=True)
    
    # Return top 150 results (Increased from 100)
    final_results = results[:150]
    SEARCH_CACHE[q] = final_results
    
    print(f"✅ Found {len(final_results)} items in {time.time() - start_time:.2f}s")
    return jsonify(final_results)

if __name__ == "__main__":
    # app.run is for local testing only. 
    # In production, Gunicorn handles the running.
    app.run(host='0.0.0.0', port=5000)
