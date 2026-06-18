import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

import os

ROBLOX_API_KEY = os.environ.get("roblox_api_key")

@app.route('/search')
def search_song():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query"}), 400

    # 1. Call Deezer API
    deezer_url = f"https://api.deezer.com/search?q={query}"
    deezer_resp = requests.get(deezer_url).json()
    
    if not deezer_resp.get('data'):
        return jsonify({"error": "No results"}), 404

    track = deezer_resp['data'][0]
    title = track['title']
    artist = track['artist']['name']
    cover_url = track['album']['cover_xl']  # Best quality

    # 2. Download the album art
    img_data = requests.get(cover_url).content

    # 3. Upload to Roblox as a TEMPORARY asset (free, expires in ~24 hours)
    upload_url = "https://apis.roblox.com/asset-delivery/v1/asset"
    headers = {"x-api-key": ROBLOX_API_KEY}
    files = {
        'file': ('cover.jpg', img_data, 'image/jpeg')
    }
    data = {
        'temporary': 'true'  # Crucial to avoid Robux charges
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

    asset_id = upload_resp.json().get('assetId')

    # 4. Return exactly what Roblox needs
    return jsonify({
        "title": title,
        "artist": artist,
        "assetId": asset_id
    })

import os

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
