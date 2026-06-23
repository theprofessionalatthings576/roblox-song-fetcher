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
    "one", "but", "where", "when", "why", "who", 
    "his", "her", "their", "you", "your", "him", "hers", "on", "in", "at", "ever", "if", 
    "all", "yours", "beside", "under", "over", "yes", "no", "not", "win", "lose", "end", 
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

# ── Track sourcing ─────────────────────────────────────────────────────────────

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
