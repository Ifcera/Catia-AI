"""PyInstaller giriş noktası: CATIA-Asistan.exe"""
import sys

from catia_aibot.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
