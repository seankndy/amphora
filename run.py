#!/usr/bin/env python3
"""Development launcher — run Amphora without installing it."""

import sys

from amphora.application import main

if __name__ == "__main__":
    sys.exit(main())
