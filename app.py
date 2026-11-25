from __future__ import annotations

import socket
import threading
import traceback
import webbrowser
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from qrgen.errors import InputError
from qrgen.export_3mf import build_3mf, filename_for
from qrgen.fonts import available_fonts
from qrgen.params import parse_params
from qrgen.scene import build_scene

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(ROOT / "templates"))
app.config["TEMPLATES_AUTO_RELOAD"] = True


def _payload():
    data = request.get_json(silent=True)
    if data is None:
        raise InputError("Brak danych formularza.")
    params = parse_params(data)
    return build_scene(params)


@app.get("/")
def index():
    fonts = [choice.to_json() for choice in available_fonts()]
    return render_template("index.html", fonts=fonts)


@app.get("/api/fonts")
def fonts():
    return jsonify([choice.to_json() for choice in available_fonts()])


@app.post("/api/preview")
def preview():
    try:
        scene = _payload()
    except InputError as error:
        return jsonify(error=str(error)), 400
    except Exception:
        traceback.print_exc()
        return jsonify(error="Nie udało się narysować podglądu."), 500
    return jsonify(svg=scene.to_svg(), info=scene.info())


@app.post("/api/generate")
def generate():
    try:
        scene = _payload()
        payload = build_3mf(scene)
        name = filename_for(scene)
    except InputError as error:
        return jsonify(error=str(error)), 400
    except Exception:
        traceback.print_exc()
        return jsonify(error="Nie udało się zapisać pliku 3MF."), 500
    buffer = BytesIO(payload)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="model/3mf",
        as_attachment=True,
        download_name=name,
    )


def _free_port(start: int = 5000) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit("Nie ma wolnego portu w zakresie 5000–5019.")


def main():
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"Panel tabliczki 3MF: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
