from flask import Blueprint, render_template
from .common import log

main = Blueprint("main", __name__)

# ------------------
# Routes
# ------------------

@main.route("/")
def index():
    log.debug("GET for /")
    return render_template("index.html")
