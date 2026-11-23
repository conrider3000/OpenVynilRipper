import os
from flask import Flask, Response, redirect

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
@app.route("/index.html")
def home():
    for candidate in (
        os.path.join(BASE_DIR, "index.html"),
        os.path.join(BASE_DIR, "public", "index.html"),
    ):
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8") as f:
                return Response(f.read(), mimetype="text/html; charset=utf-8")
    return Response("Landing page not found", status=404)


@app.route("/assets/<path:filename>")
def serve_assets(filename):
    for candidate in (
        os.path.join(BASE_DIR, "assets", filename),
        os.path.join(BASE_DIR, "public", "assets", filename),
    ):
        if os.path.isfile(candidate):
            with open(candidate, "rb") as f:
                return Response(f.read(), mimetype="image/png")
    return Response("Asset not found", status=404)


@app.route("/downloads/OpenVynilRipper-v2.0-Windows.zip")
def download_v2():
    return redirect(
        "https://github.com/conrider3000/OpenVynilRipper/raw/main/public/downloads/OpenVynilRipper-v2.0-Windows.zip",
        code=302,
    )


@app.route("/downloads/EZVinylTapeConverterSetup.exe")
def download_driver():
    return redirect(
        "https://github.com/conrider3000/OpenVynilRipper/raw/main/EZVinylTapeConverterSetup.exe",
        code=302,
    )
