import os
import uuid
from datetime import datetime, timezone

import requests

from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory
)


app = Flask(__name__)

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)


# ==============================
# SUPABASE SETTINGS
# ==============================

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
).rstrip("/")


SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
)


SUPABASE_BUCKET = os.environ.get(
    "SUPABASE_BUCKET",
    "sharespace"
)


# ==============================
# DASHBOARD PASSWORD
# ==============================

ADMIN_PASSWORD = os.environ.get(
    "SHARESPACE_PASSWORD",
    ""
)


MAX_IMAGE_BYTES = 8 * 1024 * 1024


# ==============================
# SUPABASE HEADERS
# ==============================

def sb_headers():

    return {
        "apikey": SUPABASE_KEY,
        "Authorization":
            f"Bearer {SUPABASE_KEY}"
    }


# ==============================
# CHECK SUPABASE
# ==============================

def require_supabase():

    if not SUPABASE_URL:
        return False, (
            "SUPABASE_URL is missing.",
            500
        )

    if not SUPABASE_KEY:
        return False, (
            "SUPABASE_SERVICE_ROLE_KEY is missing.",
            500
        )

    return True, None


# ==============================
# TIME
# ==============================

def now_utc():

    return datetime.now(
        timezone.utc
    ).isoformat()


# ==============================
# SHARE ID VALIDATION
# ==============================

def valid_share_id(value):

    if not value:
        return False

    if len(value) > 100:
        return False

    return all(
        c.isalnum() or c in "-_"
        for c in value
    )


# ==============================
# HOME
# ==============================

@app.get("/")
def home():

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# ==============================
# SHARE PAGE
# ==============================

@app.get("/share/<share_id>")
def share_page(share_id):

    if not valid_share_id(
        share_id
    ):

        return (
            "Invalid share ID",
            400
        )

    return send_from_directory(
        BASE_DIR,
        "index.html"
    )


# ==============================
# DASHBOARD PAGE
# ==============================

@app.get("/dashboard")
def dashboard():

    return send_from_directory(
        BASE_DIR,
        "dashboard.html"
    )


# ==============================
# HEALTH
# ==============================

@app.get("/api/health")
def health():

    return jsonify(
        ok=True,
        service="ShareSpace"
    )


# ==============================
# SAVE PHOTO + LOCATION
# ==============================

@app.post("/api/share/<share_id>")
def save_share(share_id):

    ok, error = require_supabase()

    if not ok:

        return jsonify(
            ok=False,
            error=error[0]
        ), error[1]


    if not valid_share_id(
        share_id
    ):

        return jsonify(
            ok=False,
            error="Invalid share ID"
        ), 400


    photo =
        request.files.get("photo")


    if not photo:

        return jsonify(
            ok=False,
            error="Photo missing"
        ), 400


    if not (
        photo.mimetype or ""
    ).startswith("image/"):

        return jsonify(
            ok=False,
            error="Invalid image"
        ), 400


    # ==========================
    # LOCATION
    # ==========================

    try:

        latitude = float(
            request.form["latitude"]
        )

        longitude = float(
            request.form["longitude"]
        )

        accuracy = float(
            request.form.get(
                "accuracy",
                "0"
            )
        )

    except (
        KeyError,
        TypeError,
        ValueError
    ):

        return jsonify(
            ok=False,
            error="Location missing or invalid"
        ), 400


    if not (
        -90 <= latitude <= 90
        and
        -180 <= longitude <= 180
    ):

        return jsonify(
            ok=False,
            error="Invalid coordinates"
        ), 400


    # ==========================
    # READ PHOTO
    # ==========================

    image_data = photo.read()


    if not image_data:

        return jsonify(
            ok=False,
            error="Empty photo"
        ), 400


    if len(image_data) > MAX_IMAGE_BYTES:

        return jsonify(
            ok=False,
            error="Photo is too large"
        ), 413


    # ==========================
    # EXTENSION
    # ==========================

    mime = (
        photo.mimetype
        or
        "image/jpeg"
    )


    ext = ".jpg"


    if mime == "image/png":

        ext = ".png"

    elif mime == "image/webp":

        ext = ".webp"


    path = (
        "photos/"
        +
        uuid.uuid4().hex
        +
        ext
    )


    # ==========================
    # UPLOAD TO SUPABASE
    # ==========================

    try:

        upload = requests.post(

            f"{SUPABASE_URL}"
            f"/storage/v1/object/"
            f"{SUPABASE_BUCKET}/"
            f"{path}",

            headers={
                **sb_headers(),
                "Content-Type": mime,
                "x-upsert": "false"
            },

            data=image_data,

            timeout=60
        )


        if upload.status_code not in (
            200,
            201
        ):

            return jsonify(
                ok=False,
                error="Photo upload failed",
                details=
                    upload.text[:500]
            ), 502


        # ======================
        # SAVE DATABASE RECORD
        # ======================

        timestamp = now_utc()


        row = {

            "share_id":
                share_id,

            "photo_path":
                path,

            "latitude":
                latitude,

            "longitude":
                longitude,

            "accuracy":
                accuracy,

            "active":
                True,

            "created_at":
                timestamp,

            "updated_at":
                timestamp

        }


        db = requests.post(

            f"{SUPABASE_URL}"
            "/rest/v1/"
            "sharespace_shares"
            "?on_conflict=share_id",

            headers={
                **sb_headers(),
                "Content-Type":
                    "application/json",

                "Prefer":
                    "resolution=merge-duplicates,"
                    "return=minimal"
            },

            json=row,

            timeout=20
        )


        if db.status_code not in (
            200,
            201,
            204
        ):

            # Cleanup uploaded photo
            try:

                requests.delete(

                    f"{SUPABASE_URL}"
                    f"/storage/v1/object/"
                    f"{SUPABASE_BUCKET}/"
                    f"{path}",

                    headers=sb_headers(),

                    timeout=20
                )

            except Exception:
                pass


            return jsonify(
                ok=False,
                error="Database save failed",
                details=db.text[:500]
            ), 502


        return jsonify(
            ok=True,
            message=
                "Photo and location saved"
        )


    except requests.RequestException as exc:

        return jsonify(
            ok=False,
            error=
                "Supabase connection failed",
            details=str(exc)
        ), 502


# ==============================
# LIVE LOCATION UPDATE
# ==============================

@app.post("/api/location/<share_id>")
def update_location(share_id):

    ok, error = require_supabase()

    if not ok:

        return jsonify(
            ok=False,
            error=error[0]
        ), error[1]


    data =
        request.get_json(
            silent=True
        ) or {}


    try:

        latitude = float(
            data["latitude"]
        )

        longitude = float(
            data["longitude"]
        )

        accuracy = float(
            data.get(
                "accuracy",
                0
            )
        )

    except (
        KeyError,
        TypeError,
        ValueError
    ):

        return jsonify(
            ok=False,
            error="Invalid location"
        ), 400


    if not (
        -90 <= latitude <= 90
        and
        -180 <= longitude <= 180
    ):

        return jsonify(
            ok=False,
            error="Invalid coordinates"
        ), 400


    response = requests.patch(

        f"{SUPABASE_URL}"
        "/rest/v1/"
        "sharespace_shares"
        f"?share_id=eq.{share_id}",

        headers={
            **sb_headers(),

            "Content-Type":
                "application/json",

            "Prefer":
                "return=minimal"
        },

        json={

            "latitude":
                latitude,

            "longitude":
                longitude,

            "accuracy":
                accuracy,

            "active":
                True,

            "updated_at":
                now_utc()

        },

        timeout=20
    )


    if response.status_code not in (
        200,
        204
    ):

        return jsonify(
            ok=False,
            error="Location update failed",
            details=
                response.text[:500]
        ), 502


    return jsonify(
        ok=True
    )


# ==============================
# STOP SHARING
# ==============================

@app.post("/api/stop/<share_id>")
def stop_share(share_id):

    ok, error = require_supabase()

    if not ok:

        return jsonify(
            ok=False,
            error=error[0]
        ), error[1]


    response = requests.patch(

        f"{SUPABASE_URL}"
        "/rest/v1/"
        "sharespace_shares"
        f"?share_id=eq.{share_id}",

        headers={
            **sb_headers(),

            "Content-Type":
                "application/json",

            "Prefer":
                "return=minimal"
        },

        json={
            "active": False,
            "updated_at": now_utc()
        },

        timeout=20
    )


    if response.status_code not in (
        200,
        204
    ):

        return jsonify(
            ok=False,
            error="Stop failed"
        ), 502


    return jsonify(
        ok=True
    )


# ==============================
# ADMIN LOGIN
# ==============================

@app.post("/api/admin/login")
def admin_login():

    if not ADMIN_PASSWORD:

        return jsonify(
            ok=False,
            error=
              "SHARESPACE_PASSWORD is not configured."
        ), 500


    supplied =
        request.headers.get(
            "X-Admin-Password",
            ""
        )


    if supplied != ADMIN_PASSWORD:

        return jsonify(
            ok=False,
            error="Wrong password"
        ), 401


    return jsonify(
        ok=True
    )


# ==============================
# CHECK ADMIN PASSWORD
# ==============================

def admin_password_ok():

    if not ADMIN_PASSWORD:
        return False

    supplied =
        request.headers.get(
            "X-Admin-Password",
            ""
        )

    return supplied == ADMIN_PASSWORD


# ==============================
# DASHBOARD DATA
# ==============================

@app.get("/api/dashboard")
def dashboard_data():

    if not admin_password_ok():

        return jsonify(
            ok=False,
            error="Unauthorized"
        ), 401


    ok, error = require_supabase()

    if not ok:

        return jsonify(
            ok=False,
            error=error[0]
        ), error[1]


    response = requests.get(

        f"{SUPABASE_URL}"
        "/rest/v1/"
        "sharespace_shares"
        "?select="
        "share_id,"
        "photo_path,"
        "latitude,"
        "longitude,"
        "accuracy,"
        "active,"
        "created_at,"
        "updated_at"
        "&order=updated_at.desc"
        "&limit=100",

        headers=sb_headers(),

        timeout=20
    )


    if response.status_code != 200:

        return jsonify(
            ok=False,
            error="Database read failed",
            details=
                response.text[:500]
        ), 502


    items =
        response.json()


    # ==========================
    # CREATE SIGNED PHOTO URL
    # ==========================

    for item in items:

        item["photo_url"] = None


        photo_path =
            item.get(
                "photo_path"
            )


        if not photo_path:
            continue


        try:

            signed = requests.post(

                f"{SUPABASE_URL}"
                "/storage/v1/object/sign/"
                f"{SUPABASE_BUCKET}/"
                f"{photo_path}",

                headers={
                    **sb_headers(),
                    "Content-Type":
                        "application/json"
                },

                json={
                    "expiresIn": 3600
                },

                timeout=20
            )


            if signed.status_code == 200:

                signed_path =
                    signed.json().get(
                        "signedURL"
                    )


                if signed_path:

                    if signed_path.startswith(
                        "http"
                    ):

                        item[
                            "photo_url"
                        ] = signed_path

                    else:

                        item[
                            "photo_url"
                        ] = (
                            f"{SUPABASE_URL}"
                            "/storage/v1"
                            f"{signed_path}"
                        )

        except Exception:
            pass


    return jsonify(
        ok=True,
        items=items
    )


# ==============================
# START SERVER
# ==============================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
        )
