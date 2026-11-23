"""``python -m plotroom`` runs the same command line as the ``plotroom`` script."""

import sys

from plotroom.app.cli import main

if __name__ == "__main__":
    sys.exit(main())
