import requests
from flask import Flask, request, jsonify
import os
import json

app = Flask(__name__)

ROBLOX_API_KEY = os.environ.get("roblox_api_key")


@app.route('/search')
def search_song():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    # 1. Deezer API
    deezer_url = f"https://api.deezer.com/search?q={query}"
    deezer_resp = requests.get(deezer_url).json()

    if not deezer_resp.get('data'):
        return jsonify({"error": "No results"}), 404

    track = deezer_resp['data'][0]
    title = track['title']
    artist = track['artist']['name']
    cover_url = track['album']['cover_xl']

    # 2. Download image
    img_data = requests.get(cover_url).content

    # 3. Roblox upload (CORRECT endpoint)
    upload_url = "https://apis.roblox.com/assets/v1/assets"

    headers = {
        "x-api-key": ROBLOX_API_KEY
    }

    metadata = {
        "assetType": "Decal",
        "displayName": f"{title} - {artist}",
        "description": "Uploaded via Flask bot",
        "creationContext": {
            "creator": {
                "userId": 1
            }
        }
    }

    files = {
        "fileContent": ("cover.jpg", img_data, "image/jpeg")
    }

    data = {
        "request": json.dumps(metadata)
    }

    upload_resp = requests.post(
        upload_url,
        headers=headers,
        files=files,
        data=data
    )

    print("Status:", upload_resp.status_code)
    print("Response:", upload_resp.text)

    if upload_resp.status_code != 200:
        return jsonify({
            "error": "Failed to upload to Roblox",
            "status": upload_resp.status_code,
            "details": upload_resp.text
        }), 500

    asset_id = upload_resp.json().get("assetId")

    return jsonify({
        "title": title,
        "artist": artist,
        "assetId": asset_id
    })


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
