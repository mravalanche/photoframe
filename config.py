import os

def load_env_file(env_path=".env"):
    """Manually loads key-value pairs from a .env file into environment variables."""
    if not os.path.exists(env_path):
        return
    
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):  # Ignore empty lines and comments
                continue
            key, value = line.split("=", 1)
            os.environ[key] = value.strip()

def load_config(app):
    """Loads .env variables into app.config dynamically."""
    load_env_file()
    for key, value in os.environ.items():
        app.config[key] = value
