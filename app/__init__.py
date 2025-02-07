from flask import Flask
from config import load_config
import secrets

def create_app():
    app = Flask(__name__)
    load_config(app)
    app.secret_key = secrets.token_hex(32)

    from .routes import main
    app.register_blueprint(main)

    return app
