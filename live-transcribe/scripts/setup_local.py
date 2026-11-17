"""Prepare this checkout's Python environment. Does not record or download a model."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv

parser = argparse.ArgumentParser(description=__doc__)
parser.parse_args()
if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10 or newer is required; Python 3.12 is recommended.')
root = Path(__file__).resolve().parents[1]
environment = root / '.venv'
venv.EnvBuilder(with_pip=True).create(environment)
python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=True)
subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / 'requirements.txt')], check=True)
print(f'Ready: {python}\nNext: use scripts/live_transcribe.py prepare-model --model base')
