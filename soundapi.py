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
_artist_fan_cache = {}  # artist_id -> nb_fan, persists for the server's lifetime


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
    """
    Fetches the nb_fan (follower count) for a Deezer artist.
    Cached indefinitely per server session since fan counts change slowly.
    Falls back to 0 on any failure so rarity gracefully becomes Common.
    """
    if not artist_id:
        return 0
    if artist_id in _artist_fan_cache:
        return _artist_fan_cache[artist_id]

    nb_fan = 0
    try:
        resp = requests.get(f"https://api.deezer.com/artist/{artist_id}", timeout=5).json()
        nb_fan = int(resp.get("nb_fan", 0))
    except Exception:
        pass

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


def build_track_result(track_data, artist_id=None):
    """
    Shared helper that builds the result dict sent to Roblox for both
    the search and random endpoints. Fetches genre and nb_fan in one place.
    artist_id can be passed directly if already extracted from the track object.
    """
    resolved_artist_id = artist_id or track_data.get("artist", {}).get("id")
    album_id = track_data.get("album", {}).get("id")

    return {
        "id":       track_data["id"],
        "title":    censor(track_data["title"]),
        "artist":   censor(track_data["artist"]["name"]),
        "genre":    get_genre(album_id),
        "rank":     track_data.get("rank", 0),
        "nb_fan":   get_artist_fans(resolved_artist_id),
        "explicit": is_explicit(track_data),
    }


@app.route('/search')
def search_song():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    deezer_url = f"https://api.deezer.com/search?q={query}"
    deezer_resp = requests.get(deezer_url).json()
    raw_results = deezer_resp.get('data')
    if not raw_results:
        return jsonify({"error": "No results"}), 404

    results = []
    for track in raw_results:
        results.append(build_track_result(track))
        if len(results) >= MAX_RESULTS:
            break

    return jsonify({"results": results})


@app.route('/random')
def random_song():
    max_id = get_anchor_max_id()

    for _ in range(RANDOM_MAX_ATTEMPTS):
        track_id = random.randint(1, max_id)

        try:
            resp = requests.get(f"https://api.deezer.com/track/{track_id}", timeout=5).json()
        except Exception:
            continue

        if not resp or resp.get("error") or not resp.get("title") or not resp.get("artist"):
            continue

        # /track/{id} returns a flat artist object (id + name directly),
        # so we pass artist id explicitly rather than relying on the nested
        # structure that build_track_result expects from search results
        artist_id = resp.get("artist", {}).get("id")

        return jsonify({"result": build_track_result(resp, artist_id=artist_id)})

    return jsonify({"error": "Could not find a track after several attempts, try again"}), 503


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
