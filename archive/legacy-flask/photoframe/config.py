import os
from pathlib import Path
from photoframe.common import log

# Possible locations for .env file. Either in repo root, or app route
ENV_OPTIONS = [
    Path(__file__).parent/".env",
    Path(__file__).parent.parent/".env"
]

def load_env_file():
    """Manually loads key-value pairs from a .env file into environment variables."""
    log.debug("Trying to load config...")
    success = False

    config = dict()

    for env_file in ENV_OPTIONS:
        if not env_file.is_file():
            log.info(f"Didn't load config file from {env_file.absolute()}")
            continue

        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):  # Ignore empty lines and comments
                        continue
                    key, value = line.split("=", 1)
                    os.environ[key] = value.strip()
                    config[key] = value.strip()
        except Exception:
            continue
        else:
            log.info(f"Successfully loaded .env variables from {env_file.absolute()}")
            success = True

    if success:
        log.debug("Loaded .env file(s)")
        return config
    else:
        log.error("Tried to load .env files, but was unable to")
        log.critical("Unless .env variables have been set manually, this application is unlikely to work")

def load_config(app, all_environ=False):
    """Loads .env variables into app.config dynamically."""
    config = load_env_file()
    
    if all_environ:
        for key, value in os.environ.items():
            app.config[key] = value
    
    # Local config always after, as it will overwrite any envs

    if not config:
        return None
    for key, value in config.items():
        app.config[key] = value
