"""Local CPU live transcription. No cloud API; capture starts only with `record`."""
import argparse
import json
import os
from pathlib import Path
import queue
import threading
import time
import wave
from datetime import datetime, timezone

CACHE = Path(os.environ.get('LIVE_TRANSCRIBE_MODELS', str(Path(__file__).resolve().parents[1] / 'models')))


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    # Windows readers/antivirus may briefly hold the destination without delete
    # sharing. Retrying preserves atomic reads without aborting audio capture.
    for attempt in range(40):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(.05)


def rows(session):
    path = session / 'transcript.jsonl'
    result = []
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # A concurrent writer may be midway through the last line.
    return sorted(result, key=lambda r: (r['start'], r['source']))


class WindowWriter:
    """Persist overlapping audio windows; jobs hold paths rather than audio in RAM."""
    def __init__(self, session, source, rate, channels, window, overlap, jobs, offset=0):
        self.session, self.source = session, source
        self.rate, self.channels = rate, channels
        self.size = round(window * rate)
        self.overlap = round(overlap * rate)
        self.jobs, self.offset = jobs, offset
        self.buffer = bytearray()
        self.start, self.lower, self.index = 0, 0., 0

    def push(self, data):
        self.buffer.extend(data)
        width = self.channels * 2
        while len(self.buffer) >= self.size * width:
            upper = (self.start + self.size - self.overlap / 2) / self.rate
            self.emit(bytes(self.buffer[:self.size * width]), upper)
            step = self.size - self.overlap
            del self.buffer[:step * width]
            self.start += step

    def emit(self, data, upper):
        path = self.session / 'audio' / f'{self.source}-{self.index:06}.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(self.channels)
            audio.setsampwidth(2)
            audio.setframerate(self.rate)
            audio.writeframes(data)
        self.jobs.put(dict(path=str(path), source=self.source,
                           offset=self.offset + self.start / self.rate,
                           lower=self.offset + self.lower,
                           upper=self.offset + upper))
        self.lower = upper
        self.index += 1

    def finish(self):
        end = self.start / self.rate + len(self.buffer) / (self.channels * 2 * self.rate)
        if self.buffer and end > self.lower:
            self.emit(bytes(self.buffer), end + .001)
        self.buffer.clear()


def selected_words(segment, job):
    return [w for w in (segment.words or [])
            if job['lower'] <= job['offset'] + (w.start + w.end) / 2 < job['upper']]


class Session:
    def __init__(self, args):
        self.args = args
        self.path = args.session.resolve()
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / 'audio').mkdir()
        self.jobs = queue.Queue()
        self.stop = threading.Event()
        self.capture_done = threading.Event()
        self.lock = threading.RLock()
        self.state = dict(pid=os.getpid(), state='loading', model=args.model,
                          created_utc=datetime.now(timezone.utc).isoformat(),
                          sources={}, errors=[], processed_jobs=0,
                          transcription_seconds=0., audio_window_seconds=0.)
        self.update()

    def update(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)
            self.state['heartbeat_utc'] = datetime.now(timezone.utc).isoformat()
            self.state['pending_jobs'] = self.jobs.qsize()
            atomic_json(self.path / 'status.json', self.state)

    def error(self, message):
        with self.lock:
            self.state['errors'].append(str(message))
            self.update()
        self.stop.set()

    def load(self):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(self.args.model, device='cpu', compute_type='int8',
                                  cpu_threads=self.args.threads, download_root=str(CACHE),
                                  local_files_only=not self.args.allow_download)

    def worker(self):
        try:
            while True:
                job = self.jobs.get()
                if job is None:
                    return
                begin = time.monotonic()
                segments, info = self.model.transcribe(
                    job['path'], language=self.args.language, beam_size=1,
                    temperature=0., condition_on_previous_text=False,
                    word_timestamps=True, vad_filter=True,
                    vad_parameters={'min_silence_duration_ms': 500})
                for segment in segments:
                    words = selected_words(segment, job)
                    if not words:
                        continue
                    row = dict(source=job['source'],
                               start=round(job['offset'] + words[0].start, 3),
                               end=round(job['offset'] + words[-1].end, 3),
                               text=''.join(w.word for w in words).strip(),
                               language=info.language,
                               avg_logprob=segment.avg_logprob,
                               no_speech_prob=segment.no_speech_prob)
                    with (self.path / 'transcript.jsonl').open('a', encoding='utf-8') as out:
                        out.write(json.dumps(row, ensure_ascii=False) + '\n')
                    with (self.path / 'transcript.txt').open('a', encoding='utf-8') as out:
                        out.write(f"[{stamp(row['start'])} --> {stamp(row['end'])}] {row['source']}: {row['text']}\n")
                with self.lock:
                    source = self.state['sources'].setdefault(job['source'], {})
                    source['processed_through_seconds'] = round(job['upper'], 3)
                    self.state['processed_jobs'] += 1
                    self.state['transcription_seconds'] += time.monotonic() - begin
                    self.state['audio_window_seconds'] += info.duration
                    self.update()
        except Exception as exc:
            self.error(f'Transcription failed; saved audio retained: {exc}')

    def export(self):
        transcript = rows(self.path)
        atomic_json(self.path / 'transcript.json', {'segments': transcript})
        (self.path / 'transcript.txt').write_text(''.join(
            f"[{stamp(r['start'])} --> {stamp(r['end'])}] {r['source']}: {r['text']}\n"
            for r in transcript), encoding='utf-8')

    def run(self):
        worker = None
        try:
            self.load()
            worker = threading.Thread(target=self.worker, daemon=True)
            worker.start()
            if self.args.command == 'replay':
                self.update(state='replaying')
                # File replay exercises the same windowing and inference without recording.
                import av
                with av.open(str(self.args.input)) as media:
                    resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
                    writer = WindowWriter(self.path, 'replay', 16000, 1,
                                          self.args.window, self.args.overlap, self.jobs)
                    count = 0
                    for frame in media.decode(audio=0):
                        for converted in resampler.resample(frame):
                            data = converted.to_ndarray().tobytes()
                            writer.push(data)
                            count += len(data) // 2
                    for converted in resampler.resample(None):
                        data = converted.to_ndarray().tobytes()
                        writer.push(data)
                        count += len(data) // 2
                    writer.finish()
                    with self.lock:
                        self.state['sources'].setdefault('replay', {})['captured_seconds'] = count / 16000
            else:
                self.capture()
        except KeyboardInterrupt:
            self.stop.set()
        except Exception as exc:
            self.error(exc)
        finally:
            self.update(state='draining')
            if worker is not None and worker.is_alive():
                self.jobs.put(None)
                while worker.is_alive():
                    worker.join(timeout=1)
                    self.update()
            self.export()
            self.update(state='failed' if self.state['errors'] else 'stopped')
        print(json.dumps(self.state, indent=2), flush=True)
        return 1 if self.state['errors'] else 0

    def capture(self):
        from audio_backend import AudioBackend
        with AudioBackend() as audio:
            devices = [(name, audio.select(name, getattr(self.args, name + '_device')))
                       for name in ('mic', 'system') if self.args.source in (name, 'both')]
            if len(devices) == 2 and devices[0][1]['index'] == devices[1][1]['index']:
                raise ValueError('Microphone and system sources must use different input devices')
            streams, collectors = [], []
            problems = queue.SimpleQueue()
            epoch = time.monotonic()
            try:
                for name, device in devices:
                    rate, channels = device['rate'], min(2, device['channels'])
                    blocks = queue.Queue(maxsize=1000)
                    def callback(data, status, blocks=blocks, name=name):
                        # No file I/O, locks, or inference in the real-time callback.
                        if self.stop.is_set():
                            return
                        if status:
                            problems.put(f'{name}: audio discontinuity ({status}); check devices and restart')
                        try:
                            blocks.put_nowait((time.monotonic(), data))
                        except queue.Full:
                            problems.put(f'{name}: capture queue overflow')
                    stream = audio.open(device, callback)
                    streams.append(stream)
                    thread = threading.Thread(target=self.collect,
                                              args=(name, device, rate, channels, blocks, epoch), daemon=True)
                    collectors.append(thread)
                    thread.start()
                for stream in streams:
                    stream.start_stream()
                self.update(state='recording')
                while not self.stop.wait(.5):
                    while not problems.empty():
                        self.error(problems.get())
                    if (self.path / 'STOP').exists():
                        self.stop.set()
                    if self.args.duration and time.monotonic() - epoch >= self.args.duration:
                        self.stop.set()
                    if not self.stop.is_set() and any(not stream.is_active() for stream in streams):
                        self.error('An audio stream stopped unexpectedly')
                    self.update()
            finally:
                self.stop.set()
                try:
                    for stream in streams:
                        try:
                            stream.close()
                        except Exception as exc:
                            self.error(f'Closing audio stream: {exc}')
                    while not problems.empty():
                        self.error(problems.get())
                finally:
                    self.capture_done.set()
                    for thread in collectors:
                        thread.join()

    def collect(self, name, device, rate, channels, blocks, epoch):
        writer = None
        frames = 0
        try:
            while not self.capture_done.is_set() or not blocks.empty():
                try:
                    arrival, data = blocks.get(timeout=.2)
                except queue.Empty:
                    continue
                duration = len(data) / (channels * 2 * rate)
                if writer is None:
                    offset = max(0., arrival - epoch - duration)
                    writer = WindowWriter(self.path, name, rate, channels,
                                          self.args.window, self.args.overlap, self.jobs, offset)
                # Some loopback devices deliver no callbacks while silent. Insert silence
                # when a material wall-clock gap appears, keeping later timestamps aligned.
                expected = epoch + writer.offset + frames / rate
                gap = arrival - duration - expected
                if gap > .25:
                    missing = round(gap * rate)
                    silence = bytes(round(.1 * rate) * channels * 2)
                    while missing > 0:
                        n = min(missing, len(silence) // (channels * 2))
                        writer.push(silence[:n * channels * 2])
                        frames += n
                        missing -= n
                writer.push(data)
                frames += len(data) // (channels * 2)
                with self.lock:
                    self.state['sources'].setdefault(name, {}).update(
                        device=device['name'], captured_seconds=round(writer.offset + frames / rate, 3))
            if writer:
                writer.finish()
        except Exception as exc:
            self.error(f'{name} capture failed: {exc}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('devices', help='List devices without recording')
    prepare = sub.add_parser('prepare-model', help='Download/cache a model without recording')
    prepare.add_argument('--model', choices=['tiny', 'base', 'small', 'medium'], default='base')
    for command in ('record', 'replay'):
        p = sub.add_parser(command)
        p.add_argument('--session', type=Path, required=True, help='New directory; existing sessions are never overwritten')
        p.add_argument('--model', choices=['tiny', 'base', 'small', 'medium'], default='base')
        p.add_argument('--language', default=None)
        p.add_argument('--threads', type=int, default=min(4, os.cpu_count() or 4))
        p.add_argument('--window', type=float, default=10)
        p.add_argument('--overlap', type=float, default=2)
        p.add_argument('--allow-download', action='store_true', help='Permit model download; otherwise cached models only')
        if command == 'record':
            p.add_argument('--source', choices=['mic', 'system', 'both'], default='both')
            p.add_argument('--mic-device', type=int)
            p.add_argument('--system-device', type=int)
            p.add_argument('--duration', type=float, help='Optional maximum capture duration in seconds')
        else:
            p.add_argument('--input', type=Path, required=True)
    for command in ('status', 'stop', 'read'):
        p = sub.add_parser(command)
        p.add_argument('--session', type=Path, required=True)
        if command == 'read':
            p.add_argument('--since', type=float, default=0, help='Seconds from session start')
            p.add_argument('--search', help='Literal case-insensitive phrase; include neighboring entries')
            p.add_argument('--context', type=int, default=2)
    args = parser.parse_args()
    if args.command == 'prepare-model':
        from faster_whisper import WhisperModel
        WhisperModel(args.model, device='cpu', compute_type='int8', download_root=str(CACHE))
        print(f'Model {args.model} cached in {CACHE}')
        return 0
    if args.command == 'devices':
        from audio_backend import AudioBackend
        with AudioBackend() as audio:
            for device in audio.devices():
                print(json.dumps(device))
        return 0
    if args.command in ('record', 'replay'):
        if args.threads < 1 or not 0 < args.overlap < args.window or args.window > 60:
            parser.error('Require threads >= 1 and 0 < overlap < window <= 60')
        if args.command == 'record' and args.duration is not None and args.duration <= 0:
            parser.error('--duration must be positive')
        if round(args.overlap * 16000) < 1 or round((args.window - args.overlap) * 16000) < 1:
            parser.error('Window and overlap are too close or too small')
        if args.session.exists():
            parser.error('Session directory exists; use a new directory')
        return Session(args).run()
    status = json.loads((args.session / 'status.json').read_text(encoding='utf-8'))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(status['heartbeat_utc'])).total_seconds()
    status['heartbeat_age_seconds'] = round(age, 1)
    if status['state'] not in ('stopped', 'failed') and age > 30:
        status['warning'] = 'Heartbeat stale; process may have exited. Saved audio/transcript may be incomplete.'
    if args.command == 'stop':
        if status['state'] not in ('stopped', 'failed'):
            (args.session / 'STOP').touch()
        print('Stop requested; poll status until stopped or failed. Pending transcription will drain.')
    elif args.command == 'status':
        print(json.dumps(status, indent=2))
    else:
        print(json.dumps({'state': status['state'], 'sources': status['sources'],
                          'pending_jobs': status['pending_jobs'], 'errors': status['errors'],
                          'heartbeat_age_seconds': status['heartbeat_age_seconds'],
                          'warning': status.get('warning')}, indent=2))
        transcript = [r for r in rows(args.session) if r['end'] >= args.since]
        indices = set(range(len(transcript)))
        if args.search:
            indices = set()
            for i, row in enumerate(transcript):
                if args.search.casefold() in row['text'].casefold():
                    indices.update(range(max(0, i - args.context), min(len(transcript), i + args.context + 1)))
        for i in sorted(indices):
            r = transcript[i]
            print(f"[{stamp(r['start'])} --> {stamp(r['end'])}] {r['source']}: {r['text']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
