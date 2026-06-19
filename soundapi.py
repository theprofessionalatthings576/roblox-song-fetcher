import os
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


def is_explicit(track):
    """
    Returns True if a Deezer track is flagged as explicit.
    Checks the lyrics flag (album art flags are no longer relevant
    since cover art support has been removed).

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


@app.route('/search')
def search_song():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    # Optional escape hatch: /search?q=...&allow_explicit=true
    allow_explicit = request.args.get('allow_explicit', 'false').lower() == 'true'

    # 1. Deezer API
    deezer_url = f"https://api.deezer.com/search?q={query}"
    deezer_resp = requests.get(deezer_url).json()
    results = deezer_resp.get('data')
    if not results:
        return jsonify({"error": "No results"}), 404

    # 2. Pick the first track that passes the explicit filter
    track = None
    if allow_explicit:
        track = results[0]
    else:
        for candidate in results:
            if not is_explicit(candidate):
                track = candidate
                break
        if track is None:
            return jsonify({
                "error": "No clean (non-explicit) results found",
                "checked": len(results)
            }), 404

    # 3. Censor any blacklisted words in the title/artist before returning
    title = censor(track['title'])
    artist = censor(track['artist']['name'])

    return jsonify({
        "title": title,
        "artist": artist,
        "explicit": is_explicit(track)
    })


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
