import os
import json
import base64
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = 8080

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DATA_FILE = os.path.join(BASE_DIR, "shared.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)

if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)


def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class Handler(SimpleHTTPRequestHandler):

    def send_json(self, obj, status=200):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/latest":
            data = load_data()
            self.send_json(data)
            return

        if path == "/dashboard":
            self.path = "/dashboard.html"

        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path

        length = int(self.headers.get("Content-Length", "0"))

        if length > 15 * 1024 * 1024:
            self.send_json({"error": "Request too large"}, 413)
            return

        body = self.rfile.read(length)

        try:
            data = json.loads(body.decode("utf-8"))
        except:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        shared = load_data()

        if path == "/api/location":
            if "latitude" not in data or "longitude" not in data:
                self.send_json({"error": "Location missing"}, 400)
                return

            shared["latitude"] = data["latitude"]
            shared["longitude"] = data["longitude"]
            shared["accuracy"] = data.get("accuracy")
            shared["location_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            save_data(shared)

            self.send_json({"ok": True})
            return

        if path == "/api/photo":
            photo = data.get("photo", "")

            if not photo.startswith("data:image/"):
                self.send_json({"error": "Invalid photo"}, 400)
                return

            try:
                header, encoded = photo.split(",", 1)
                image_data = base64.b64decode(encoded)

                filename = "sharespace-photo.jpg"
                filepath = os.path.join(UPLOAD_DIR, filename)

                with open(filepath, "wb") as f:
                    f.write(image_data)

                shared["photo"] = "/uploads/" + filename
                shared["photo_time"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                save_data(shared)

                self.send_json({
                    "ok": True,
                    "photo": shared["photo"]
                })
            except Exception as e:
                self.send_json({"error": "Photo save failed"}, 500)

            return

        if path == "/api/stop":
            shared = {}
            save_data(shared)

            self.send_json({"ok": True})
            return

        self.send_json({"error": "Not found"}, 404)


print("=" * 40)
print("ShareSpace is running")
print("Website:   http://127.0.0.1:8080")
print("Dashboard: http://127.0.0.1:8080/dashboard")
print("=" * 40)

server = ThreadingHTTPServer((HOST, PORT), Handler)
server.serve_forever()
