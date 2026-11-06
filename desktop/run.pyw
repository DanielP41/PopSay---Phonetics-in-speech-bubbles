"""Arranque de la app (lo ejecuta PopSay.exe con el Python incluido)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from popsay.app import main  # noqa: E402

sys.exit(main())
