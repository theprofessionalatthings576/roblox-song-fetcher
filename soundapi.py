import os
import time
import random
import requests
from flask import Flask, request, jsonify

# better-profanity ships with a maintained, well-tested default wordlist
# (swears, slurs, sexual terms) and censors matches with asterisks.
# Install with: pip install better-profanity
from better_profanity import profanity

app = Flask(__name__)
profanity.load_censor_words()

# Add any extra game-specific terms you want blocked, e.g.:
# profanity.add_censor_words(["yourword1", "yourword2"])

MAX_RESULTS = 10
RANDOM_MAX_ATTEMPTS = 25
ANCHOR_TTL_SECONDS = 3600  # re-check Deezer's current catalog size hourly

_anchor_cache = {"max_id": None, "timestamp": 0}


def is_explicit(track):
    """
    Returns True if a Deezer track is flagged as explicit (lyrics).
    Kept for informational purposes only - no longer used to filter results.

    Deezer's explicit_content_lyrics codes:
      0 = Not Explicit
      1 = Explicit
      2 = Unknown
      3 = Edited (clean version)
    """
    if track.get("explicit_lyrics"):
        return True
    if track.get("explicit_content_lyrics") == 1:
        return True
    return False


def censor(text):
    """Replace blacklisted words (swears, slurs, sexual terms) with asterisks."""
    if not text:
        return text
    return profanity.censor(text)


def get_anchor_max_id():
    """
    Returns a current, self-updating upper bound for Deezer track IDs by
    checking the global chart. This avoids hardcoding a max ID that would
    go stale as Deezer's catalog grows.
    """
    now = time.time()
    if _anchor_cache["max_id"] and (now - _anchor_cache["timestamp"] < ANCHOR_TTL_SECONDS):
        return _anchor_cache["max_id"]

    try:
        resp = requests.get("https://api.deezer.com/chart/0/tracks?limit=50", timeout=5).json()
        track_ids = [t["id"] for t in resp.get("data", []) if "id" in t]
        if track_ids:
            # Pad the highest chart ID a bit, since chart tracks are popular
            # but not necessarily Deezer's very newest additions
            anchor = int(max(track_ids) * 1.2)
            _anchor_cache["max_id"] = anchor
            _anchor_cache["timestamp"] = now
            return anchor
    except Exception:
        pass

    # Fallback if the chart call fails: reuse the last known good anchor,
    # or a conservative default if we've never successfully fetched one
    return _anchor_cache["max_id"] or 4_000_000_000


@app.route('/search')
def search_song():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    # 1. Deezer API
    deezer_url = f"https://api.deezer.com/search?q={query}"
    deezer_resp = requests.get(deezer_url).json()
    raw_results = deezer_resp.get('data')
    if not raw_results:
        return jsonify({"error": "No results"}), 404

    # 2. Censor text, cap at MAX_RESULTS (no explicit filtering)
    results = []
    for track in raw_results:
        results.append({
            "title": censor(track['title']),
            "artist": censor(track['artist']['name']),
            "explicit": is_explicit(track)
        })
        if len(results) >= MAX_RESULTS:
            break

    return jsonify({"results": results})


@app.route('/random')
def random_song():
    """
    Returns one genuinely random track from across Deezer's catalog by
    hitting /track/{id} with a random ID, rather than biasing toward
    whatever a search query happens to surface.
    """
    max_id = get_anchor_max_id()

    for _ in range(RANDOM_MAX_ATTEMPTS):
        track_id = random.randint(1, max_id)

        try:
            resp = requests.get(f"https://api.deezer.com/track/{track_id}", timeout=5).json()
        except Exception:
            continue

        # Gaps in the ID space (deleted tracks, unused IDs) return an
        # error or incomplete object - skip and try another ID
        if not resp or resp.get("error") or not resp.get("title") or not resp.get("artist"):
            continue

        return jsonify({
            "result": {
                "title": censor(resp["title"]),
                "artist": censor(resp["artist"]["name"]),
                "explicit": is_explicit(resp)
            }
        })

    return jsonify({"error": "Could not find a track after several attempts, try again"}), 503


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
