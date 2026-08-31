import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET = os.environ.get("SUPABASE_BUCKET", "sharespace")

IST = ZoneInfo("Asia/Kolkata")


def now_ist():
    return datetime.now(IST).isoformat(timespec="seconds")


def headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


def create_bucket():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    url = f"{SUPABASE_URL}/storage/v1/bucket"
    payload = {
        "id": BUCKET,
        "name": BUCKET,
        "public": False,
        "file_size_limit": 10485760,
    }

    try:
        r = requests.post(
            url,
            headers={**headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )

        if r.status_code in (200, 201):
            print("Supabase bucket ready.")
        elif r.status_code == 409:
            print("Supabase bucket already exists.")
        else:
            print("Bucket response:", r.status_code, r.text[:300])
    except Exception as e:
        print("Bucket check failed:", e)


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/dashboard")
def dashboard():
    return send_from_directory(BASE_DIR, "dashboard.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "ShareSpace",
        "time_ist": now_ist()
    })


@app.route("/api/share", methods=["POST"])
def share():
    """
    Receives photo + location only after the browser has obtained
    the user's explicit permission.
    """

    if not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({
            "ok": False,
            "error": "Supabase environment variables are missing."
        }), 500

    photo = request.files.get("photo")

    latitude = request.form.get("latitude")
    longitude = request.form.get("longitude")
    accuracy = request.form.get("accuracy")

    if not photo:
        return jsonify({
            "ok": False,
            "error": "No photo received."
        }), 400

    ext = os.path.splitext(photo.filename or "")[1].lower()

    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        ext = ".jpg"

    filename = f"{uuid.uuid4().hex}{ext}"
    path = f"photos/{filename}"

    content_type = photo.mimetype or "image/jpeg"
    file_bytes = photo.read()

    upload_url = (
        f"{SUPABASE_URL}/storage/v1/object/"
        f"{BUCKET}/{path}"
    )

    try:
        upload = requests.post(
            upload_url,
            headers={
                **headers(),
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            data=file_bytes,
            timeout=60,
        )

        if upload.status_code not in (200, 201):
            return jsonify({
                "ok": False,
                "error": "Supabase photo upload failed",
                "details": upload.text[:500]
            }), 502

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 502

    created = now_ist()

    # Store metadata in Supabase database.
    row = {
        "photo_path": path,
        "latitude": float(latitude) if latitude else None,
        "longitude": float(longitude) if longitude else None,
        "accuracy": float(accuracy) if accuracy else None,
        "created_at": created,
    }

    try:
        db = requests.post(
            f"{SUPABASE_URL}/rest/v1/sharespace_shares",
            headers={
                **headers(),
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json=row,
            timeout=20,
        )

        if db.status_code not in (200, 201):
            return jsonify({
                "ok": False,
                "error": "Database insert failed",
                "details": db.text[:500]
            }), 502

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 502

    return jsonify({
        "ok": True,
        "message": "Shared successfully",
        "photo_path": path,
        "time_ist": created
    })


@app.route("/api/latest")
def latest():
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/sharespace_shares"
            "?select=*"
            "&order=id.desc"
            "&limit=1",
            headers=headers(),
            timeout=20,
        )

        if r.status_code != 200:
            return jsonify({
                "ok": False,
                "error": r.text[:500]
            }), 502

        rows = r.json()

        if not rows:
            return jsonify({
                "ok": True,
                "data": None
            })

        data = rows[0]

        photo_path = data.get("photo_path")

        if photo_path:
            signed = requests.post(
                f"{SUPABASE_URL}/storage/v1/object/sign/"
                f"{BUCKET}/{photo_path}",
                headers={
                    **headers(),
                    "Content-Type": "application/json",
                },
                json={"expiresIn": 3600},
                timeout=20,
            )

            if signed.status_code == 200:
                result = signed.json()
                signed_path = result.get("signedURL")

                if signed_path:
                    if signed_path.startswith("http"):
                        data["photo_url"] = signed_path
                    else:
                        data["photo_url"] = (
                            f"{SUPABASE_URL}/storage/v1"
                            f"{signed_path}"
                        )

        return jsonify({
            "ok": True,
            "data": data
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    create_bucket()

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
