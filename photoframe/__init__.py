from flask import Flask, url_for
import threading
from photoframe.config import load_config
from photoframe.common import log
from werkzeug.middleware.proxy_fix import ProxyFix
from photoframe.google_photos_client import GooglePhotosClient, GOOGLE_AUTH_CONFIG
from photoframe.photo_handler import photo_updater
from photoframe.store import store
import secrets

def create_app():
    app = Flask(__name__)
    log.debug(f"Photoframe started. {app.root_path = }")
    
    app.config["app_route"] = app.root_path
    load_config(app)
    app.secret_key = secrets.token_hex(32)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
    app.extensions['photo_client'] = GooglePhotosClient(
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        app_route=app.config["app_route"],
        album_id=store.get("album_id", None),
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
