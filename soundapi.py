import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

ROBLOX_API_KEY = "4L0q7SUZxkGFiHuf6KW0Ql2Lf2eB+d3oK8hRaElR45DII0cnZXlKaGJHY2lPaUpTVXpJMU5pSXNJbXRwWkNJNkluTnBaeTB5TURJeExUQTNMVEV6VkRFNE9qVXhPalE1V2lJc0luUjVjQ0k2SWtwWFZDSjkuZXlKaGRXUWlPaUpTYjJKc2IzaEpiblJsY201aGJDSXNJbWx6Y3lJNklrTnNiM1ZrUVhWMGFHVnVkR2xqWVhScGIyNVRaWEoyYVdObElpd2lZbUZ6WlVGd2FVdGxlU0k2SWpSTU1IRTNVMVZhZUd0SFJtbElkV1kyUzFjd1VXd3lUR1l5WlVJclpETnZTemhvVW1GRmJGSTBOVVJKU1RCamJpSXNJbTkzYm1WeVNXUWlPaUl6T1RRd05EQTNNRFExSWl3aVpYaHdJam94TnpneE9ERXlOVFE1TENKcFlYUWlPakUzT0RFNE1EZzVORGtzSW01aVppSTZNVGM0TVRnd09EazBPWDAuYWRIdnppTjZLbE1ScHB0NFFYb203Y01sMkI4R1RGWVUtNGFIT2l6ZTZadk5mYlY4NkpHelY4aDRXOEJPLTF0QU94TVEySUEyNzBTWTBmTldhdFYtS2c4WG1kRmtleS1WTkRGNXpoZW43VHUySHRHV1p6MTRmYXBKaVR1S3VIb3JIZ3U3eVlLRXFkLW5lekF4eTBqei0wTVhnd0NnNk92czNjZ2NlZkxLZzJuSDhKRjUwLVd6X3ZHWVNXSVAxaFVWbW5lS3FhQ1ZseVlCazV6U1lYbkdiTUhHVS1YYXdSTUhVNUtEUGlXZTczOGRHS3hVaG1XRnFwcWFhck9lVWgxWklRVGtlY3NndWM3Z2c1Z2V0YkNXSUZpQXpzT2ZEaTYwYjk5VUQ0YzN3V2J5UlQzWmZSUzNCaUFIZUppWkF2cWY4eXlJZndiaEY0YkhFeDFFVHpnWkV3"

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
    
    upload_resp = requests.post(upload_url, headers=headers, files=files, data=data)
    if upload_resp.status_code != 200:
        return jsonify({"error": "Failed to upload to Roblox"}), 500

    asset_id = upload_resp.json().get('assetId')

    # 4. Return exactly what Roblox needs
    return jsonify({
        "title": title,
        "artist": artist,
        "assetId": asset_id
    })

if __name__ == '__main__':
    app.run(port=5000)
