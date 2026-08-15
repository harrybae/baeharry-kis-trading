import os
from flask import Blueprint, make_response, render_template, send_from_directory

pages_bp = Blueprint("pages", __name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pages_bp.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(ROOT_DIR, "web", "static"), "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


@pages_bp.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp
