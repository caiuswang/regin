"""Entry point: `python -m hook_manager <EventName>`."""

import sys

from .runner import main

if __name__ == '__main__':
    sys.exit(main())
