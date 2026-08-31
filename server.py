import os
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
import requests

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET = os.environ.get("SUPABASE_BUCKET", "sharespace")
ADMIN_PASSWORD = os.environ.get("SHARESPACE_PASSWORD", "ChangeThisPassword")

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

def require_sb():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False, ("Supabase environment variables are missing.", 500)
    return True, None

@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/share/<share_id>")
def share_page(share_id):
    return send_from_directory(BASE_DIR, "index.html")

@app.get("/dashboard")
def dashboard():
    return send_from_directory(BASE_DIR, "index.html")

@app.post("/api/share/<share_id>")
def save_share(share_id):
    ok, error = require_sb()
    if not ok:
        return jsonify(ok=False, error=error[0]), error[1]

    photo = request.files.get("photo")
    if not photo or not (photo.mimetype or "").startswith("image/"):
        return jsonify(ok=False, error="Image missing"), 400

    try:
        lat = float(request.form["latitude"])
        lng = float(request.form["longitude"])
        accuracy = float(request.form.get("accuracy", "0"))
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="Location missing or invalid"), 400

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(ok=False, error="Invalid coordinates"), 400

    data = photo.read()
    if not data or len(data) > 8 * 1024 * 1024:
        return jsonify(ok=False, error="Invalid or oversized image"), 413

    ext = ".jpg"
    if photo.mimetype == "image/png":
        ext = ".png"
    elif photo.mimetype == "image/webp":
        ext = ".webp"

    path = f"photos/{uuid.uuid4().hex}{ext}"

    upload = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
        headers={**sb_headers(), "Content-Type": photo.mimetype or "image/jpeg", "x-upsert": "false"},
        data=data,
        timeout=60,
    )
    if upload.status_code not in (200, 201):
        return jsonify(ok=False, error="Photo upload failed", details=upload.text[:500]), 502

    now = datetime.now(timezone.utc).isoformat()

    row = {
        "share_id": share_id,
        "photo_path": path,
        "latitude": lat,
        "longitude": lng,
        "accuracy": accuracy,
        "active": True,
        "updated_at": now,
        "created_at": now,
    }

    db = requests.post(
        f"{SUPABASE_URL}/rest/v1/sharespace_shares",
        headers={**sb_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"},
        json=row,
        timeout=20,
    )

    if db.status_code not in (200, 201):
        # Avoid leaving an orphan photo when the DB insert fails.
        requests.delete(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
            headers=sb_headers(),
            timeout=20,
        )
        return jsonify(ok=False, error="Database insert failed", details=db.text[:500]), 502

    return jsonify(ok=True, message="Photo and location saved")

@app.post("/api/location/<share_id>")
def update_location(share_id):
    ok, error = require_sb()
    if not ok:
        return jsonify(ok=False, error=error[0]), error[1]

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["latitude"])
        lng = float(data["longitude"])
        accuracy = float(data.get("accuracy", 0))
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="Invalid location"), 400

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(ok=False, error="Invalid coordinates"), 400

    now = datetime.now(timezone.utc).isoformat()

    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/sharespace_shares?share_id=eq.{share_id}",
        headers={**sb_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={
            "latitude": lat,
            "longitude": lng,
            "accuracy": accuracy,
            "updated_at": now,
            "active": True,
        },
        timeout=20,
    )
    if r.status_code not in (200, 204):
        return jsonify(ok=False, error="Location update failed", details=r.text[:500]), 502

    return jsonify(ok=True)

@app.post("/api/stop/<share_id>")
def stop_share(share_id):
    ok, error = require_sb()
    if not ok:
        return jsonify(ok=False, error=error[0]), error[1]

    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/sharespace_shares?share_id=eq.{share_id}",
        headers={**sb_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"},
        json={"active": False},
        timeout=20,
    )
    if r.status_code not in (200, 204):
        return jsonify(ok=False, error="Stop failed"), 502
    return jsonify(ok=True)

@app.get("/api/share/<share_id>")
def get_share(share_id):
    ok, error = require_sb()
    if not ok:
        return jsonify(ok=False, error=error[0]), error[1]

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/sharespace_shares?share_id=eq.{share_id}&select=*",
        headers=sb_headers(),
        timeout=20,
    )
    if r.status_code != 200:
        return jsonify(ok=False, error="Database read failed", details=r.text[:500]), 502

    rows = r.json()
    if not rows:
        return jsonify(ok=False, error="Share not found"), 404

    x = rows[0]
    photo_url = None
    if x.get("photo_path"):
        signed = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET}/{x['photo_path']}",
            headers={**sb_headers(), "Content-Type": "application/json"},
            json={"expiresIn": 3600},
            timeout=20,
        )
        if signed.status_code in (200, 201):
            token = signed.json().get("signedURL") or signed.json().get("signedUrl")
            if token:
                photo_url = token if token.startswith("http") else f"{SUPABASE_URL}/storage/v1{token}"

    return jsonify(ok=True, item={**x, "photo_url": photo_url})

@app.get("/api/dashboard")
def dashboard_data():
    if request.headers.get("X-Admin-Password", "") != ADMIN_PASSWORD:
        return jsonify(ok=False, error="Unauthorized"), 401

    ok, error = require_sb()
    if not ok:
        return jsonify(ok=False, error=error[0]), error[1]

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/sharespace_shares?select=*&order=created_at.desc&limit=100",
        headers=sb_headers(),
        timeout=20,
    )
    if r.status_code != 200:
        return jsonify(ok=False, error="Database read failed", details=r.text[:500]), 502

    items = r.json()
    for x in items:
        x["photo_url"] = None
        if x.get("photo_path"):
            signed = requests.post(
                f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET}/{x['photo_path']}",
                headers={**sb_headers(), "Content-Type": "application/json"},
                json={"expiresIn": 3600},
                timeout=20,
            )
            if signed.status_code in (200, 201):
                token = signed.json().get("signedURL") or signed.json().get("signedUrl")
                if token:
                    x["photo_url"] = token if token.startswith("http") else f"{SUPABASE_URL}/storage/v1{token}"

    return jsonify(ok=True, items=items)

@app.get("/api/health")
def health():
    return jsonify(ok=True, service="ShareSpace")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
    
