"""Follow a live transcript in a console. Closing this viewer never stops capture."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time


def safe(text):
    return ''.join(c for c in str(text) if c >= ' ' and c != '\x7f')


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


class Tail:
    """Keep partial UTF-8/JSON records until their terminating newline arrives."""
    def __init__(self, path):
        self.path = Path(path)
        self.position = 0
        self.pending = b''

    def read(self):
        if not self.path.exists():
            return []
        with self.path.open('rb') as stream:
            if self.path.stat().st_size < self.position:
                self.position, self.pending = 0, b''
            stream.seek(self.position)
            block = stream.read()
            self.position = stream.tell()
        lines = (self.pending + block).split(b'\n')
        self.pending = lines.pop()
        records = []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if all(k in row for k in ('start', 'end', 'source', 'text')):
                    records.append(row)
            except (UnicodeDecodeError, json.JSONDecodeError):
                print('[viewer] Skipped an invalid transcript record.', flush=True)
        return records


def status_line(status):
    coverage = []
    for name, source in status.get('sources', {}).items():
        captured = source.get('captured_seconds', 0)
        processed = source.get('processed_through_seconds', 0)
        coverage.append(f'{name}: processed {stamp(processed)}, captured {stamp(captured)}')
    return (f"{status.get('state', 'waiting')} | " + (' | '.join(coverage) or 'Waiting for audio')
            + f" | queued windows: {status.get('pending_jobs', 0)}")


def watch(session, last=20, poll=.5, heartbeat=15):
    print('LIVE TRANSCRIPT  |  Ask questions in your Codex chat', flush=True)
    print(f'Session: {session}', flush=True)
    print('Local speech recognition. Closing this window leaves recording running.', flush=True)
    print('Use "Stop transcription and summarize" in Codex to end recording.\n', flush=True)
    tail = Tail(session / 'transcript.jsonl')
    first = True
    previous_state, previous_report = None, 0
    waiting_since = time.monotonic()
    try:
        while True:
            records = tail.read()
            if first:
                records = records[-last:] if last else records
                first = False
            for row in records:
                print(f"[{stamp(row['start'])} - {stamp(row['end'])}] "
                      f"{safe(row['source']).upper():6}  {safe(row['text'])}", flush=True)
            try:
                status = json.loads((session / 'status.json').read_text(encoding='utf-8'))
            except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                if time.monotonic() - waiting_since > 30:
                    print('[viewer] Cannot read session status. Recording state is unknown.', flush=True)
                    return 2
                time.sleep(poll)
                continue
            waiting_since = time.monotonic()
            state = status.get('state')
            now = time.monotonic()
            if state != previous_state or now - previous_report >= heartbeat:
                print('\n[status] ' + status_line(status), flush=True)
                previous_state, previous_report = state, now
            if state in ('stopped', 'failed'):
                # Drain a final append if it raced the first read in this iteration.
                for row in tail.read():
                    print(f"[{stamp(row['start'])} - {stamp(row['end'])}] "
                          f"{safe(row['source']).upper():6}  {safe(row['text'])}", flush=True)
                for error in status.get('errors', []):
                    print('[capture error] ' + safe(error), flush=True)
                print(f'\nRecording {state}. Transcript saved in {session}', flush=True)
                return 1 if state == 'failed' else 0
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(status['heartbeat_utc'])).total_seconds()
                if age > 30:
                    print('[viewer] Capture heartbeat is stale. Check the recording in Codex.', flush=True)
                    return 2
            except (KeyError, ValueError):
                pass
            time.sleep(poll)
    except KeyboardInterrupt:
        print('\nViewer closed. Recording continues; stop it from Codex.', flush=True)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--last', type=int, default=20, help='Initial entries; 0 shows all history')
    parser.add_argument('--poll', type=float, default=.5)
    args = parser.parse_args()
    if args.last < 0 or args.poll <= 0:
        parser.error('Require last >= 0 and poll > 0')
    if not args.session.is_dir():
        parser.error('Session directory does not exist')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    return watch(args.session.resolve(), args.last, args.poll)


if __name__ == '__main__':
    raise SystemExit(main())
