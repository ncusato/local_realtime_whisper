"""Set up the bundled live-transcribe skill without recording."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "live-transcribe/scripts/setup_local.py"), run_name="__main__")
