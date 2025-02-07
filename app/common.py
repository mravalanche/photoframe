import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()
DEBUG = False

__all__= [
    'console',
    'DEBUG',
    'log'
]

# ----------------------------------------
# Logging
# ----------------------------------------

# Formatters

time_format = '[%Y-%m-%d %H:%M:%S]'

## My default, preferred text formatter
log_formatter = logging.Formatter('%(asctime)s [%(levelname)8s] %(message)s \t(%(filename)s:%(lineno)s)', time_format)

## If using Rich, it needs its own formatter with _just_ the message, becuase it fills in all of the other bits itself
rich_formatter = logging.Formatter('%(message)s')

# Rich Stream Handler (preferred)

rich_handler = RichHandler(rich_tracebacks=True, console=console, log_time_format=time_format)
rich_handler.setFormatter(rich_formatter)
rich_handler.setLevel(logging.DEBUG if DEBUG else logging.INFO)

# Basic Stream Handler (fallback)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.DEBUG if DEBUG else logging.INFO)


# Logging Setup

log = logging.getLogger(__name__)
log.addHandler(rich_handler)
log.setLevel(logging.DEBUG)  # This always needs to be DEBUG (highest) because the handlers set their own levels

pad = "-" * 30
log.debug(f"{pad} LOGGER STARTED {pad}")
log.debug("Currently logging at DEBUG level - check you need this, becuase it can get very noisy. Does DEBUG==True?")
