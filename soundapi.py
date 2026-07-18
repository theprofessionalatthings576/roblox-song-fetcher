import os
import time
import random
import requests
from flask import Flask, request, jsonify
from better_profanity import profanity

app = Flask(__name__)
profanity.load_censor_words()

MAX_RESULTS = 10
RANDOM_MAX_ATTEMPTS = 25
ANCHOR_TTL_SECONDS = 3600

_anchor_cache = {"max_id": None, "timestamp": 0}
_genre_cache = {}
_artist_fan_cache = {}

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
]

TOP_ARTIST_IDS = [
    "13",  "4050205", "12246", "1176900", "145468192", "10799102", "259", "9635624", "1562681", "6982223",   # example Deezer artist IDs
    # ...fill in verified IDs for artists you want guaranteed to appear
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_genre(album_id):
    if not album_id:
        return "Unknown"
    if album_id in _genre_cache:
        return _genre_cache[album_id]

    genre_name = "Unknown"
    try:
        resp = requests.get(f"https://api.deezer.com/album/{album_id}", timeout=5).json()
        genres = resp.get("genres", {}).get("data", [])
        if genres:
            genre_name = genres[0].get("name", "Unknown")
    except Exception:
        pass

    _genre_cache[album_id] = genre_name
    return genre_name


def get_artist_fans(artist_id):
    if not artist_id:
        return 0

    if artist_id in _artist_fan_cache:
        return _artist_fan_cache[artist_id]

    try:
        resp = requests.get(
            f"https://api.deezer.com/artist/{artist_id}",
            timeout=5
        ).json()

        nb_fan = int(resp.get("nb_fan", 0))

    except Exception:
        nb_fan = 0

    _artist_fan_cache[artist_id] = nb_fan
    return nb_fan


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


def get_anchor_max_id():
    now = time.time()
    if _anchor_cache["max_id"] and (now - _anchor_cache["timestamp"] < ANCHOR_TTL_SECONDS):
        return _anchor_cache["max_id"]

    try:
        resp = requests.get("https://api.deezer.com/chart/0/tracks?limit=50", timeout=5).json()
        track_ids = [t["id"] for t in resp.get("data", []) if "id" in t]
        if track_ids:
            anchor = int(max(track_ids) * 1.2)
            _anchor_cache["max_id"] = anchor
            _anchor_cache["timestamp"] = now
            return anchor
    except Exception:
        pass

    return _anchor_cache["max_id"] or 4_000_000_000

def deezer_get(url, max_retries=2):
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=5).json()
        except Exception:
            return None
        err = resp.get("error")
        if err:
            if err.get("code") == 4 and attempt < max_retries - 1:
                time.sleep(0.3)
            else:
                return None
        else:
            return resp
    return None
    
def build_track_result(track_data, artist_id=None, nb_fan=None):
    resolved_artist_id = artist_id or track_data.get("artist", {}).get("id")
    album_id = track_data.get("album", {}).get("id")
    resolved_nb_fan = nb_fan if nb_fan is not None else get_artist_fans(resolved_artist_id)

    return {
        "id":      track_data["id"],
        "title":   censor(track_data["title"]),
        "artist":  censor(track_data["artist"]["name"]),
        "genre":   get_genre(album_id),
        "rank":    track_data.get("rank", 0),
        "nb_fan":  resolved_nb_fan,
        "explicit": is_explicit(track_data),
    }


# ── Track sourcing ─────────────────────────────────────────────────────────────

def generate_random_seed():
    """
    Generate either a single seed or a two-word seed phrase.
    """

    if random.random() < 0.5:
        return random.choice(SEARCH_SEEDS)

    return (
        f"{random.choice(SEARCH_SEEDS)} "
        f"{random.choice(SEARCH_SEEDS)}"
    )

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
    
def get_candidate_tracks(
    fan_min,
    fan_max,
    target_candidates=10
):
    """
    Build a small randomized pool of matching tracks.
    Fast enough for live API use.
    """

    candidates = []
    seen_tracks = set()

    for _ in range(8):

        seed = generate_random_seed()

        offset = random.randint(0, 10) * 25

        try:
            resp = requests.get(
                f"https://api.deezer.com/search"
                f"?q={seed}"
                f"&limit=25"
                f"&index={offset}",
                timeout=5
            ).json()

        except Exception:
            continue

        tracks = resp.get("data", [])

        if not tracks:
            continue

        random.shuffle(tracks)

        for track in tracks:

            if is_explicit(track):
                continue

            track_id = track.get("id")

            if not track_id:
                continue

            if track_id in seen_tracks:
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

            candidates.append(
                (track, artist_id, nb_fan)
            )

            if len(candidates) >= target_candidates:
                return candidates

    return candidates


def get_random_track_for_tier(fan_min, fan_max):
    """
    Randomly pick a track from a pool of valid candidates.
    """

    candidates = get_candidate_tracks(
        fan_min=fan_min,
        fan_max=fan_max,
        target_candidates=10
    )

    if not candidates:
        return None, None, None

    return random.choice(candidates)


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.route('/artist_search')
def artist_search():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    try:
        deezer_resp = requests.get(
            f"https://api.deezer.com/search/artist?q={query}&limit=10",
            timeout=5
        ).json()
    except Exception:
        return jsonify({"error": "Deezer request failed"}), 502

    raw_results = deezer_resp.get("data")
    if not raw_results:
        return jsonify({"error": "No results"}), 404

    results = []
    for artist in raw_results:
        results.append({
            "id":     str(artist.get("id", "")),
            "name":   censor(artist.get("name", "Unknown")),
            "nb_fan": int(artist.get("nb_fan", 0)),
        })

    return jsonify({"results": results})


@app.route('/artist_tracks')
def artist_tracks():
    artist_id = request.args.get('id')
    if not artist_id:
        return jsonify({"error": "Missing id"}), 400

    try:
        # Fetch the artist's top tracks (up to 50)
        deezer_resp = requests.get(
            f"https://api.deezer.com/artist/{artist_id}/top?limit=50",
            timeout=5
        ).json()
    except Exception:
        return jsonify({"error": "Deezer request failed"}), 502

    tracks = [t for t in deezer_resp.get("data", []) if not is_explicit(t)]
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
        resp = deezer_get(
            f"https://api.deezer.com/artist/{artist_id}/albums?limit={page_size}&index={index}"
        )
        if not resp:
            break
        page = resp.get("data", [])
        if not page:
            break
        all_albums.extend(page)
        if len(page) < page_size:
            break  # last page
        index += page_size

    print(f"[get_artist_albums] artist {artist_id}: fetched {len(all_albums)} releases")
    for album in all_albums:
        print(f"  -> record_type={album.get('record_type')!r}  title={album.get('title')!r}")

    all_albums.sort(key=lambda a: ALBUM_TYPE_PRIORITY.get(a.get("record_type", ""), 1))
    return all_albums[:MAX_ALBUMS_PER_ARTIST]

def get_album_tracks(album_id):
    resp = deezer_get(f"https://api.deezer.com/album/{album_id}/tracks?limit=100")
    return resp.get("data", []) if resp else []

@app.route('/popular_artists')
def popular_artists():
    results = []
    for artist_id in TOP_ARTIST_IDS:
        try:
            resp = requests.get(f"https://api.deezer.com/artist/{artist_id}", timeout=5).json()
            results.append({
                "id": str(resp.get("id", "")),
                "name": censor(resp.get("name", "Unknown")),
                "nb_fan": int(resp.get("nb_fan", 0)),
            })
        except Exception:
            continue

    results.sort(key=lambda a: a["nb_fan"], reverse=True)
    return jsonify({"results": results})

ARTIST_SONGS_TIME_BUDGET = 18  # seconds — stay safely under Render/Roblox timeouts

@app.route('/artist_songs')
def artist_songs():
    artist_id = request.args.get('id')
    if not artist_id:
        return jsonify({"error": "Missing id"}), 400

    cached = _artist_songs_cache.get(artist_id)
    now = time.time()
    if cached and (now - cached["timestamp"] < ARTIST_SONGS_TTL_SECONDS) and not cached.get("partial"):
        songs = cached["songs"]
        return jsonify({
            "artist_id": artist_id,
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

    # Only cache (and only for the full TTL) if we actually got everything.
    # Partial results get a short TTL so a retry soon can fill in the rest.
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
        return jsonify({
            "error": "Invalid tier"
        }), 400

    fan_min, fan_max = TIER_FAN_RANGES[tier]

    track, artist_id, nb_fan = get_random_track_for_tier(
        fan_min,
        fan_max
    )

    if not track:
        return jsonify({
            "error": "Could not find a track after several attempts"
        }), 503

    return jsonify({
        "result": build_track_result(
            track,
            artist_id=artist_id,
            nb_fan=nb_fan
        )
    })
