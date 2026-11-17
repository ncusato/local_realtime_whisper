import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('backend', Path(__file__).parents[1] / 'live-transcribe/scripts/audio_backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class AudioAdapters(unittest.TestCase):
    def mac(self):
        sd = MagicMock()
        sd.default.device = (0, 1)
        sd.query_devices.return_value = [
            dict(name='MacBook Microphone', max_input_channels=1, default_samplerate=48000),
            dict(name='Speakers', max_input_channels=0, default_samplerate=48000),
            dict(name='BlackHole 2ch', max_input_channels=2, default_samplerate=48000)]
        return sd

    def test_mac_requires_explicit_system_input(self):
        with patch.object(sys, 'platform', 'darwin'), patch.dict(sys.modules, {'sounddevice': self.mac()}):
            with backend.AudioBackend() as audio:
                self.assertEqual(audio.select('mic')['index'], 0)
                self.assertEqual(audio.select('system', 2)['name'], 'BlackHole 2ch')
                with self.assertRaisesRegex(ValueError, '--system-device'):
                    audio.select('system')
                with self.assertRaisesRegex(ValueError, 'microphone is a loopback'):
                    audio.select('mic', 2)

    def test_mac_callback_copies_audio_buffer_and_propagates_status(self):
        sd, received = self.mac(), []
        with patch.object(sys, 'platform', 'darwin'), patch.dict(sys.modules, {'sounddevice': sd}):
            with backend.AudioBackend() as audio:
                stream = audio.open(audio.select('mic'), lambda data, status: received.append((data, status)))
                callback = sd.RawInputStream.call_args.kwargs['callback']
                data = bytearray(b'\x00\x01')
                callback(data, 1, None, False)
                data[0] = 9
                self.assertEqual(received[0], (b'\x00\x01', None))
                callback(data, 1, None, 'input overflow')
                self.assertEqual(received[1][1], 'input overflow')
                stream.start_stream()
                stream.close()
                sd.RawInputStream.return_value.start.assert_called_once()
                sd.RawInputStream.return_value.close.assert_called_once()

    def test_windows_uses_wasapi_default_microphone(self):
        pa = MagicMock()
        raw = pa.PyAudio.return_value
        raw.get_host_api_info_by_type.return_value = {'defaultInputDevice': 14}
        raw.get_device_count.return_value = 1
        # Check selection independently of real device indices or a microphone.
        with patch.object(sys, 'platform', 'win32'), patch.dict(sys.modules, {'pyaudiowpatch': pa}):
            with backend.AudioBackend() as audio:
                audio.devices = lambda: [dict(index=14, name='Mic', loopback=False)]
                self.assertEqual(audio.select('mic')['index'], 14)
                with self.assertRaisesRegex(ValueError, 'loopback input'):
                    audio.select('system', 14)
            raw.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
