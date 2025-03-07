import os
import pickle

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_from_directory, current_app

from photoframe.common import log, DEBUG
from photoframe.photo_handler import list_photos
from photoframe.store import store
from photoframe.google_photos_client import GOOGLE_AUTH_CONFIG

from google_auth_oauthlib.flow import InstalledAppFlow

main = Blueprint("main", __name__)
photo = Blueprint("photo", __name__, url_prefix="/photo")


# ------------------
# Helper functions
# ------------------


def get_photo_client():
    """Fetch the persistent photo_client from Flask's app extensions."""
    photo_client = current_app.extensions['photo_client']

    if not GOOGLE_AUTH_CONFIG["redirect_uris"]:
        GOOGLE_AUTH_CONFIG["redirect_uris"] = url_for('main.oauth_callback', _external=True)
    
    return photo_client


def bool_from_input(input, default=False)->bool:
    if isinstance(input, str):
        input = input.lower()
    match input:
        case "true" |  "1" | "yes" | 1 | True:
            return True
        case "false" | "0" | "no" | 0 | False | None:
            return False
        case _:
            return default
        

def auth_needed(photo_client):
    if not photo_client.service:
        flash("Not Authenticated. You need to authenticate first.", "warning")
        return redirect(url_for("main.index"))


# ------------------
# Settings
# ------------------

SETTINGS = {
    "transitions": ["normal", "pinned", "random"],
    "speeds": {1800:"30 Minutes", 3600:"1 Hour", 7200:"2 Hours", 21600:"6 Hours", 43200:"12 Hours", 86400:"1 Day", 30:"30 Seconds"}
}

# ------------------
# Routes
# ------------------

@main.context_processor
def inject_debug():
    return dict(DEBUG=DEBUG)


@main.route("/")
def index():
    photo_client = get_photo_client()
    authenticated = True if photo_client.service else False
    return render_template(
        "index.html",
        authenticated=authenticated,
        selected_album=store.get("album_title"),
        downloaded_photos=len(list_photos(photo_client)),
        album_size=store.get("album_size"),
        downloaded=store.get("downloaded"),
        current_photo=store.get("current_photo"),
        transition=store.get("transition"),
        speed=SETTINGS["speeds"].get(store.get("speed"))
    )


@main.route("/authenticate", methods=["POST"])
def authenticate():
    force_str = request.args.get("force", "false").lower()
    force = force_str in ("true", "1", "yes")

    photo_client = get_photo_client()
    photo_client.authenticate(force=force)
    auth_url = photo_client.authenticate(force=force)

    if not auth_url and not force:
        flash("Already Authenticated")
        return redirect(url_for('main.index'))

    return redirect(auth_url)


@main.route("/albums")
def albums():
    photo_client = get_photo_client()
    
    if (redirect_response := auth_needed(photo_client)):
        return redirect_response

    return render_template(
        "albums.html",
        albums=photo_client.album_dict
    )

@main.route("/store_album", methods=["POST"])
def store_album():
    log.debug(request.form.to_dict())
    album_title = request.form.get("album_title")
    album_id = request.form.get("album_id")
    album_size = request.form.get("album_size")
    store.set("album_title", album_title)
    store.set("album_id", album_id)
    store.set("album_size", int(album_size))
    store.set("current_photo", "")
    store.set("downloaded", False)
    flash("Album Selected", "success")
    return redirect(url_for("main.index"))


@main.route("/photos")
def photos():
    return render_template(
        "photos.html",
        photo_list=list_photos(get_photo_client()),
        current_photo=store.get("current_photo")
    )


@main.route("/settings")
def settings():
    return render_template(
        "settings.html",
        transitions=SETTINGS["transitions"],
        current_transition=store.get("transition"),
        speeds=SETTINGS["speeds"],
        current_speed=store.get("speed"),
        all_config=store.read_all()
    )


@main.route("/save_settings", methods=["POST"])
def save_settings():
    log.debug(request.form.to_dict())
    transition = request.form.get("transition")
    speed = request.form.get("speed")
    store.set("transition", transition.lower())
    store.set("speed", int(speed))
    flash("Settings saved", "success")
    return redirect(url_for("main.settings"))


@main.route("/store_photo", methods=["POST"])
def store_photo():
    log.debug(request.form.to_dict())
    current_photo = request.form.get("photo")
    pinned = bool_from_input(request.form.get("pinned"))
    store.set("current_photo", current_photo)
    if pinned:
        store.set("transition", "pinned")
    elif store.get("transition") in("pinned", None):
        store.set("transition", "normal")
    if pinned:
        flash("Photo Pinned", "info")
    else:
        flash("Photo Selected", "success")
    return redirect(url_for("main.index"))


@photo.route("/<path:filename>")
def serve_photos(filename):
    photo_client = get_photo_client()
    photos_dir = photo_client.photo_directory
    return send_from_directory(photos_dir, filename)


@main.route("/oauth_callback")
def oauth_callback():
    """Handles the OAuth callback from Google after user authentication."""
    photo_client = get_photo_client()
    photo_client.complete_authentication(request.url)
    
    flash("Authentication successful!", "success")
    return redirect(url_for("main.index"))
