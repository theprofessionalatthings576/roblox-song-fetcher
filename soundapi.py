import os
import time
import random
import requests
from flask import Flask, request, jsonify
from better_profanity import profanity

app = Flask(__name__)
profanity.load_censor_words()

MAX_RESULTS = 10
ANCHOR_TTL_SECONDS = 3600
ARTIST_INFO_TTL_SECONDS = 3600  # fan counts / names refresh hourly instead of forever

_genre_cache = {}
_artist_info_cache = {}  # artist_id -> {"timestamp", "name", "nb_fan"}

# Persistent session for connection pooling — reused across ALL Deezer calls now
_deezer_session = requests.Session()
_deezer_session.headers.update({"User-Agent": "collect-songs-bridge/1.0"})

TIER_FAN_RANGES = {
    "Legendary": (10_000_000, None),
    "Epic":      (1_000_000, 10_000_000),
    "Rare":      (100_000,   1_000_000),
    "Uncommon":  (10_000,    100_000),
    "Common":    (0,         10_000),
}

SEARCH_SEEDS = [
    "love", "night", "day", "fire", "rain", "road", "heart", "time", "life", "dream",
    "soul", "dark", "light", "sun", "moon", "star", "blue", "red", "gold", "break",
    "fall", "rise", "run", "stay", "gone", "lost", "free", "hold", "cold", "warm",
    "deep", "high", "low", "fast", "slow", "long", "far", "near", "wild", "still",
    "new", "old", "young", "strong", "soft", "hard", "real", "true", "good", "bad",
    "war", "peace", "hope", "pain", "joy", "fear", "hate", "cry", "fly", "water",
    "end", "begin", "wait", "move", "fight", "dance", "sing", "play", "work", "live",
    "black", "white", "green", "silver", "blood", "bones", "mind", "eyes", "hands",
    "voice", "sound", "silence", "broken", "perfect", "better", "forever", "never",
    "always", "maybe", "again", "away", "back", "down", "up", "together", "alone",
    "baby", "girl", "boy", "man", "woman", "king", "queen", "angel", "devil", "ghost",
    "rock", "roll", "beat", "bass", "melody", "rhythm", "song", "music", "world", "city",
    "one", "but", "where", "when", "why", "who", "which", "turquoise", "pink", "girl",
    "his", "her", "their", "you", "your", "him", "hers", "on", "in", "at", "ever", "if",
    "all", "yours", "beside", "under", "over", "yes", "no", "not", "win", "lose", "end",
    "cat", "dog", "meow", "woof", "song", "home", "house", "the", "and", "or", "that",
    "yep", "1", "2", "3", "4", "5", "6", "7", "8", "9", "car", "maybe", "me", "take",
    "wherever", "whenever", "whom", "concern", "john", "jack", "crazy", "emotional",
    "feat", "version", "bill", "upside", "fish", "food", "banana", "lemon", "american",
    "british", "race", "better", "worse", "worst", "best", "earth", "hey", "hi", "hello",
    "hopped", "mr", "mrs", "island", "kiss", "tonight", "today", "tomorrow", "clock",
    "america", "police", "gun", "drugs", "money", "dollars", "wasn't", "isn't", "weren't",
    "won't", "can't", "don't", "tryna", "gonna", "finna", "yo", "0", "downside", "wishing",
    "daughter", "son", "mother", "father", "u", "da", "boi", "lil", "wit", "oops", "bro",
    "dawg", "cuh", "rizz", "lowkey", "you're", "forever", "infinity", "infinite", "nostalgia",
    "memories", "memory", "dj", "gal", "bros", "!", ".", ",", "stupid", "idiot", "heads", "feet",
    "drinks", "event", "movie", "film", "drinking", "date", "party", "parties", "life", "live",
    "sorry", "changes", "new york", "london", "paris", "pump", "shut up", "king", "doctor", "prince",
    "queen", "legend", "top", "bottom", "above", "beyond", "below", "innit", "bruv", "mate", "champion",
    "oi", "howdy", "hai", "hella", "darn", "god", "jesus", "lord", "town", "city", "phone", "cellphone",
]

ALBUM_TYPE_PRIORITY = {"album": 0, "ep": 1, "compilation": 1, "single": 2}

TOP_ARTIST_IDS = [
    "13",  "4050205", "12246", "1176900", "145468192", "10799102", "259", "9635624", "1562681", "6982223",   # example Deezer artist IDs
    # ...fill in verified IDs for artists you want guaranteed to appear
]


# ── Core Deezer request helper (single source of truth for retries/params) ────

def deezer_get(path_or_url, params=None, max_retries=3, base_url="https://api.deezer.com"):
    """
    Every Deezer call should go through this. Handles:
    - connection pooling via the shared session
    - proper query-string encoding via `params` (never hand-build URLs)
    - retry-with-backoff on Deezer's rate-limit error (code 4)
    Returns the parsed JSON dict on success, or None if the call ultimately
    failed — callers should always check for None rather than assume a shape.
    """
    url = path_or_url if path_or_url.startswith("http") else f"{base_url}{path_or_url}"

    for attempt in range(max_retries):
        try:
            resp = _deezer_session.get(url, params=params, timeout=5).json()
        except Exception:
            return None

        err = resp.get("error")
        if not err:
            return resp

        if err.get("code") == 4 and attempt < max_retries - 1:
            time.sleep(0.3 * (attempt + 1))  # small backoff instead of flat 0.3s
            continue
        return None

    return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_genre(album_id):
    if not album_id:
        return "Unknown"
    if album_id in _genre_cache:
        return _genre_cache[album_id]

    genre_name = "Unknown"
    resp = deezer_get(f"/album/{album_id}")
    if resp:
        genres = resp.get("genres", {}).get("data", [])
        if genres:
            genre_name = genres[0].get("name", "Unknown")

    _genre_cache[album_id] = genre_name
    return genre_name


def get_artist_info(artist_id):
    """
    Single cached lookup for everything /artist/{id} gives us (fan count +
    display name). Replaces the old get_artist_fans/get_artist_name pair,
    which each hit the same endpoint separately and doubled Deezer calls.
    TTL'd so fan counts (and therefore rarity tiers) don't lock in stale
    values forever — previously these caches never expired.
    """
    now = time.time()
    cached = _artist_info_cache.get(artist_id)
    if cached and (now - cached["timestamp"] < ARTIST_INFO_TTL_SECONDS):
        return cached

    info = {"timestamp": now, "name": "Unknown Artist", "nb_fan": 0}
    if artist_id:
        resp = deezer_get(f"/artist/{artist_id}")
        if resp:
            info["name"] = censor(resp.get("name", "Unknown Artist"))
            info["nb_fan"] = int(resp.get("nb_fan", 0) or 0)
        # If the call failed (rate-limited, network error, etc.) and we have
        # a previous good value cached, keep serving that instead of
        # collapsing to "Unknown Artist" / 0 — this is what was silently
        # happening before.
        elif cached:
            return cached

    _artist_info_cache[artist_id] = info
    return info


def get_artist_fans(artist_id):
    if not artist_id:
        return 0
    return get_artist_info(artist_id)["nb_fan"]


def get_artist_name(artist_id):
    if not artist_id:
        return "Unknown Artist"
    return get_artist_info(artist_id)["name"]


def is_explicit(track):
    if track.get("explicit_lyrics"):
        return True
    if track.get("explicit_content_lyrics") == 1:
        return True
    return False


def censor(text):
    if not text:
        return text
    return profanity.censor(text)


def build_track_result(track_data, artist_id=None, nb_fan=None):
    artist = track_data.get("artist", {}) or {}
    resolved_artist_id = artist_id or artist.get("id")
    album = track_data.get("album", {}) or {}
    album_id = album.get("id")
    resolved_nb_fan = nb_fan if nb_fan is not None else get_artist_fans(resolved_artist_id)

    return {
        "id":       track_data.get("id"),
        "title":    censor(track_data.get("title", "Unknown Title")),
        "artist":   censor(artist.get("name", "Unknown Artist")),
        "genre":    get_genre(album_id),
        "rank":     track_data.get("rank", 0),
        "nb_fan":   resolved_nb_fan,
        "explicit": is_explicit(track_data),
    }


# ── Track sourcing ─────────────────────────────────────────────────────────────

def generate_random_seed():
    """
    Generate either a single seed or a two-word seed phrase.
    """
    if random.random() < 0.5:
        return random.choice(SEARCH_SEEDS)

    return f"{random.choice(SEARCH_SEEDS)} {random.choice(SEARCH_SEEDS)}"


def get_rarity_from_fan_count(nb_fan):
    for tier, (fan_min, fan_max) in TIER_FAN_RANGES.items():
        if nb_fan >= fan_min and (fan_max is None or nb_fan < fan_max):
            return tier
    return "Common"


def get_artist_primary_genre(albums):
    """Most common genre across the artist's albums, used as a stand-in
    for a single 'artist genre' since Deezer only tags genre per-album."""
    from collections import Counter
    genre_counts = Counter()
    for album in albums:
        genre_name = get_genre(album.get("id"))
        if genre_name and genre_name != "Unknown":
            genre_counts[genre_name] += 1
    if not genre_counts:
        return "Unknown"
    return genre_counts.most_common(1)[0][0]


def get_candidate_tracks(fan_min, fan_max, target_candidates=10):
    """
    Build a small randomized pool of matching tracks.
    Fast enough for live API use.
    """
    candidates = []
    seen_tracks = set()

    for _ in range(8):
        seed = generate_random_seed()
        offset = random.randint(0, 10) * 25

        resp = deezer_get("/search", params={"q": seed, "limit": 25, "index": offset})
        if not resp:
            continue

        tracks = resp.get("data", [])
        if not tracks:
            continue

        random.shuffle(tracks)

        for track in tracks:
            if is_explicit(track):
                continue

            track_id = track.get("id")
            if not track_id or track_id in seen_tracks:
                continue

            artist_id = track.get("artist", {}).get("id")
            if not artist_id:
                continue

            nb_fan = get_artist_fans(artist_id)
            if nb_fan < fan_min:
                continue
            if fan_max is not None and nb_fan >= fan_max:
                continue

            seen_tracks.add(track_id)
            candidates.append((track, artist_id, nb_fan))

            if len(candidates) >= target_candidates:
                return candidates

    return candidates


def get_random_track_for_tier(fan_min, fan_max):
    """
    Randomly pick a track from a pool of valid candidates.
    """
    candidates = get_candidate_tracks(fan_min=fan_min, fan_max=fan_max, target_candidates=10)
    if not candidates:
        return None, None, None
    return random.choice(candidates)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/artist_search')
def artist_search():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    resp = deezer_get("/search/artist", params={"q": query, "limit": 10})
    if resp is None:
        return jsonify({"error": "Deezer request failed"}), 502

    raw_results = resp.get("data")
    if not raw_results:
        return jsonify({"error": "No results"}), 404

    results = [
        {
            "id":     str(artist.get("id", "")),
            "name":   censor(artist.get("name", "Unknown")),
            "nb_fan": int(artist.get("nb_fan", 0) or 0),
        }
        for artist in raw_results
    ]

    return jsonify({"results": results})


@app.route('/artist_tracks')
def artist_tracks():
    artist_id = request.args.get('id')
    if not artist_id:
        return jsonify({"error": "Missing id"}), 400

    resp = deezer_get(f"/artist/{artist_id}/top", params={"limit": 50})
    if resp is None:
        return jsonify({"error": "Deezer request failed"}), 502

    tracks = [t for t in resp.get("data", []) if not is_explicit(t)]
    if not tracks:
        return jsonify({"error": "No suitable tracks found"}), 404

    track = random.choice(tracks)
    return jsonify({"result": build_track_result(track)})


ARTIST_SONGS_TTL_SECONDS = 86400  # discographies rarely change, cache for a day
MAX_ALBUMS_PER_ARTIST = 40        # bound worst-case request count for huge back catalogs

_artist_songs_cache = {}  # artist_id -> {"timestamp": ..., "songs": [...]}


def get_artist_albums(artist_id):
    all_albums = []
    index = 0
    page_size = 100  # Deezer's practical max per page

    while True:
        resp = deezer_get(f"/artist/{artist_id}/albums", params={"limit": page_size, "index": index})
        if not resp:
            break
        page = resp.get("data", [])
        if not page:
            break
        all_albums.extend(page)
        if len(page) < page_size:
            break  # last page
        index += page_size

    all_albums.sort(key=lambda a: ALBUM_TYPE_PRIORITY.get(a.get("record_type", ""), 1))
    return all_albums[:MAX_ALBUMS_PER_ARTIST]


def get_album_tracks(album_id):
    resp = deezer_get(f"/album/{album_id}/tracks", params={"limit": 100})
    return resp.get("data", []) if resp else []


@app.route('/popular_artists')
def popular_artists():
    """
    Previously this route bypassed deezer_get entirely and used a raw,
    non-retrying requests.get — so a rate-limited response (which has no
    "name"/"nb_fan" keys) silently produced {"name": "Unknown", "nb_fan": 0}
    instead of retrying. That's almost certainly the source of artists
    intermittently showing as "Unknown" in IndexGui. Now routed through
    get_artist_info, which retries and falls back to the last-known-good
    cached value instead of "Unknown" on failure.
    """
    results = []
    for artist_id in TOP_ARTIST_IDS:
        info = get_artist_info(artist_id)
        if info["name"] == "Unknown Artist" and info["nb_fan"] == 0:
            # Total failure with nothing cached yet — skip rather than show a
            # fake "Unknown" entry; it'll fill in on a later request.
            continue
        results.append({
            "id": str(artist_id),
            "name": info["name"],
            "artist_name": info["name"],  # kept alongside "name" for parity with /artist_songs
            "nb_fan": info["nb_fan"],
        })

    results.sort(key=lambda a: a["nb_fan"], reverse=True)
    return jsonify({"results": results})


ARTIST_SONGS_TIME_BUDGET = 18  # seconds — stay safely under Render/Roblox timeouts

@app.route('/artist_songs')
def artist_songs():
    artist_id = request.args.get('id')
    if not artist_id:
        return jsonify({"error": "Missing id"}), 400

    artist_name = get_artist_name(artist_id)

    cached = _artist_songs_cache.get(artist_id)
    now = time.time()
    if cached and (now - cached["timestamp"] < ARTIST_SONGS_TTL_SECONDS) and not cached.get("partial"):
        songs = cached["songs"]
        return jsonify({
            "artist_id": artist_id,
            "artist_name": artist_name,
            "total": len(songs),
            "songs": songs,
            "rarity": cached["rarity"],
            "genre": cached["genre"],
        })

    albums = get_artist_albums(artist_id)
    if not albums:
        return jsonify({"error": "No albums found"}), 404

    seen_titles = set()
    songs = []
    start_time = time.time()
    hit_time_budget = False

    for album in albums:
        if time.time() - start_time > ARTIST_SONGS_TIME_BUDGET:
            hit_time_budget = True
            break

        album_id = album.get("id")
        album_title = album.get("title", "")
        if not album_id:
            continue

        for track in get_album_tracks(album_id):
            if is_explicit(track):
                continue
            title = track.get("title", "")
            title_key = title.lower().strip()
            if not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            songs.append({
                "id":    track.get("id"),
                "title": censor(title),
                "album": censor(album_title),
            })

    if not songs:
        return jsonify({"error": "No suitable tracks found"}), 404

    nb_fan = get_artist_fans(artist_id)
    rarity = get_rarity_from_fan_count(nb_fan)
    genre = get_artist_primary_genre(albums)

    _artist_songs_cache[artist_id] = {
        "timestamp": now,
        "songs": songs,
        "rarity": rarity,
        "genre": genre,
        "partial": hit_time_budget,
    }
    if hit_time_budget:
        _artist_songs_cache[artist_id]["timestamp"] = now - ARTIST_SONGS_TTL_SECONDS + 60  # expires in ~60s

    return jsonify({
        "artist_id": artist_id,
        "artist_name": artist_name,
        "total": len(songs),
        "songs": songs,
        "rarity": rarity,
        "genre": genre,
        "partial": hit_time_budget,
    })


@app.route('/random')
def random_song():
    tier = request.args.get("tier", "Common")

    if tier not in TIER_FAN_RANGES:
        return jsonify({"error": "Invalid tier"}), 400

    fan_min, fan_max = TIER_FAN_RANGES[tier]
    track, artist_id, nb_fan = get_random_track_for_tier(fan_min, fan_max)

    if not track:
        return jsonify({"error": "Could not find a track after several attempts"}), 503

    return jsonify({"result": build_track_result(track, artist_id=artist_id, nb_fan=nb_fan)})
