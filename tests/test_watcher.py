import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('watcher', Path(__file__).parents[1] / 'live-transcribe/scripts/watch_transcript.py')
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


class Watcher(unittest.TestCase):
    def test_incremental_unicode_record_is_not_lost_or_duplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'transcript.jsonl'
            tail = watcher.Tail(path)
            self.assertEqual(tail.read(), [])
            row = dict(start=1, end=2, source='mic', text='café capacity')
            encoded = (json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8')
            split = encoded.index(b'\xc3') + 1
            path.write_bytes(encoded[:split])
            self.assertEqual(tail.read(), [])
            with path.open('ab') as stream:
                stream.write(encoded[split:])
            self.assertEqual(tail.read(), [row])
            self.assertEqual(tail.read(), [])

    def test_console_control_characters_removed(self):
        self.assertEqual(watcher.safe('hello\x1b[2J\rworld'), 'hello[2Jworld')


if __name__ == '__main__':
    unittest.main()
