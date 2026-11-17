"""Check real window ownership, tail flushing, and concurrent transcript reads."""
import importlib.util
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('live', Path(__file__).parents[1] / 'live-transcribe/scripts/live_transcribe.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class Windows(unittest.TestCase):
    def test_atomic_status_retries_windows_reader_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'status.json'
            original = live.os.replace
            calls = []
            def replace(source, target):
                calls.append(1)
                if len(calls) == 1:
                    raise PermissionError('Windows reader holds destination')
                return original(source, target)
            with patch.object(live.os, 'replace', replace):
                live.atomic_json(path, {'state': 'recording'})
            self.assertEqual(json.loads(path.read_text()), {'state': 'recording'})
            self.assertEqual(len(calls), 2)

    def test_coverage_has_no_holes_or_duplicate_ownership(self):
        for length in (1, 9, 10, 11, 16, 18, 27):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as temp:
                path = Path(temp)
                (path / 'audio').mkdir()
                jobs = queue.Queue()
                writer = live.WindowWriter(path, 'mic', 100, 1, 10, 2, jobs)
                for _ in range(length * 10):
                    writer.push(bytes(20))
                writer.finish()
                windows = list(jobs.queue)
                for tick in range(length * 100):
                    when = (tick + .5) / 100
                    owners = [w for w in windows if w['lower'] <= when < w['upper']]
                    self.assertEqual(len(owners), 1, (when, windows))
                    owner = owners[0]
                    with live.wave.open(owner['path']) as audio:
                        self.assertLess(when, owner['offset'] + audio.getnframes() / audio.getframerate())

    def test_boundary_word_owned_once(self):
        segment = SimpleNamespace(words=[SimpleNamespace(start=8.8, end=9.2)])
        self.assertFalse(live.selected_words(segment, dict(lower=0, upper=9, offset=0)))
        self.assertTrue(live.selected_words(segment, dict(lower=9, upper=17, offset=0)))

    def test_partial_jsonl_and_source_order(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / 'transcript.jsonl').write_text(
                json.dumps(dict(start=2, source='system')) + '\n' +
                json.dumps(dict(start=1, source='mic')) + '\n{"start":', encoding='utf-8')
            self.assertEqual([r['start'] for r in live.rows(path)], [1, 2])


if __name__ == '__main__':
    unittest.main()
