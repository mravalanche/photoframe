from flask import Flask
import threading
from photoframe.config import load_config
from photoframe.google_photos_client import GooglePhotosClient
from photoframe.photo_handler import photo_updater
from photoframe.store import store
import secrets

def create_app():
    app = Flask(__name__)
    load_config(app)
    app.secret_key = secrets.token_hex(32)
    app.config["app_route"] = app.root_path
    app.extensions['photo_client'] = GooglePhotosClient(
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        app_route=app.config["app_route"],
        album_id=store.get("album_id", None)
    )

    from .routes import main, photo
    app.register_blueprint(main)
    app.register_blueprint(photo)

    photo_updater_thread = threading.Thread(
        target=photo_updater,
        name="Photo Updater",
        args=(app.extensions['photo_client'],),
        daemon=True
    )
    photo_updater_thread.start()

    return app
