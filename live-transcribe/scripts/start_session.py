"""Start local capture detached, then verify startup. Never starts from installation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Additional options are forwarded to live_transcribe.py record.')
    parser.add_argument('--session', type=Path, required=True)
    args, extra = parser.parse_known_args()
    session = args.session.resolve()
    if session.exists():
        parser.error('Session exists; choose a new directory')
    session.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-u', str(Path(__file__).with_name('live_transcribe.py')),
               'record', '--session', str(session), *extra]
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    out_path, err_path = Path(str(session) + '.stdout.log'), Path(str(session) + '.stderr.log')
    with out_path.open('x', encoding='utf-8') as out:
        with err_path.open('x', encoding='utf-8') as err:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err, **options)
    print(json.dumps(dict(pid=process.pid, session=str(session), stdout=str(out_path), stderr=str(err_path))), flush=True)
    for _ in range(180):
        try:
            status = json.loads((session / 'status.json').read_text(encoding='utf-8'))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            status = {}
        if status.get('state') in ('recording', 'failed', 'stopped'):
            print(json.dumps(status, indent=2))
            return 0 if status['state'] in ('recording', 'stopped') and not status.get('errors') else 1
        if process.poll() is not None:
            print('Recorder exited during startup. Read the session logs.', file=sys.stderr)
            return 1
        time.sleep(.5)
    print('Startup still pending. Capture may start later; check status, or use stop to cancel. '
          'Do not launch another copy.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
