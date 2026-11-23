import os
import mimetypes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REDIRECTS = {
    "/downloads/OpenVynilRipper-v2.0-Windows.zip": "https://github.com/conrider3000/OpenVynilRipper/raw/main/public/downloads/OpenVynilRipper-v2.0-Windows.zip",
    "/downloads/EZVinylTapeConverterSetup.exe": "https://github.com/conrider3000/OpenVynilRipper/raw/main/EZVinylTapeConverterSetup.exe",
}


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/") or "/"

    if path in REDIRECTS:
        start_response("302 Found", [("Location", REDIRECTS[path])])
        return [b""]

    if path == "/" or path == "/index.html":
        file_path = os.path.join(BASE_DIR, "public", "index.html")
        content_type = "text/html; charset=utf-8"
    else:
        clean_path = path.lstrip("/")
        file_path = os.path.join(BASE_DIR, "public", clean_path)
        guessed, _ = mimetypes.guess_type(file_path)
        content_type = guessed or "application/octet-stream"

    if os.path.isfile(file_path):
        with open(file_path, "rb") as f:
            data = f.read()
        start_response(
            "200 OK",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(data))),
                ("Cache-Control", "public, max-age=300"),
            ],
        )
        return [data]

    # Fallback to index.html
    index_path = os.path.join(BASE_DIR, "public", "index.html")
    if os.path.isfile(index_path):
        with open(index_path, "rb") as f:
            data = f.read()
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(data))),
            ],
        )
        return [data]

    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"Not Found"]
