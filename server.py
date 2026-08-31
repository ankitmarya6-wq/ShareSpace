import os
import json
import base64
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DATA_FILE = os.path.join(BASE_DIR, "shared.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)


def now_ist():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_data(data):
    temp = DATA_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, DATA_FILE)


class Handler(SimpleHTTPRequestHandler):

    def send_json(self, obj, status=200):
        raw = json.dumps(obj).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/latest":
            self.send_json(load_data())
            return

        if path == "/dashboard":
            self.path = "/dashboard.html"

        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0

        if length > 15 * 1024 * 1024:
            self.send_json({"error": "Request too large"}, 413)
            return

        body = self.rfile.read(length)

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        shared = load_data()

        # Location — only received after browser permission/consent
        if path == "/api/location":

            if "latitude" not in data or "longitude" not in data:
                self.send_json({"error": "Location missing"}, 400)
                return

            shared["latitude"] = data["latitude"]
            shared["longitude"] = data["longitude"]
            shared["accuracy"] = data.get("accuracy")
            shared["location_time"] = now_ist()

            save_data(shared)

            self.send_json({
                "ok": True,
                "location_time": shared["location_time"]
            })
            return

        # Photo — only received after camera permission/consent
        if path == "/api/photo":

            photo = data.get("photo", "")

            if not isinstance(photo, str) or not photo.startswith("data:image/"):
                self.send_json({"error": "Invalid photo"}, 400)
                return

            try:
                header, encoded = photo.split(",", 1)
                image_data = base64.b64decode(encoded, validate=True)

                if len(image_data) > 10 * 1024 * 1024:
                    self.send_json({"error": "Photo too large"}, 413)
                    return

                filename = (
                    "sharespace-"
                    + datetime.now(IST).strftime("%Y%m%d-%H%M%S-%f")
                    + "-"
                    + uuid.uuid4().hex[:8]
                    + ".jpg"
                )

                filepath = os.path.join(UPLOAD_DIR, filename)

                with open(filepath, "wb") as f:
                    f.write(image_data)

                # Remove reference to previous photo.
                old_photo = shared.get("photo")

                shared["photo"] = "/uploads/" + filename
                shared["photo_time"] = now_ist()

                save_data(shared)

                # Delete old image only after new image is safely written.
                if old_photo and old_photo.startswith("/uploads/"):
                    old_path = os.path.join(
                        UPLOAD_DIR,
                        os.path.basename(old_photo)
                    )

                    if old_path != filepath and os.path.exists(old_path):
                        try:
                            os.remove(old_path)
                        except OSError:
                            pass

                self.send_json({
                    "ok": True,
                    "photo": shared["photo"],
                    "photo_time": shared["photo_time"]
                })

            except Exception:
                self.send_json({"error": "Photo save failed"}, 500)

            return

        # Clear current session
        if path == "/api/stop":
            save_data({})
            self.send_json({"ok": True})
            return

        self.send_json({"error": "Not found"}, 404)


print("=" * 45)
print("ShareSpace")
print("Server running")
print("Timezone: Asia/Kolkata")
print("=" * 45)

server = ThreadingHTTPServer((HOST, PORT), Handler)
server.serve_forever()
