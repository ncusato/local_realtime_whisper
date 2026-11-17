"""Platform audio adapters. Importing this module never opens an audio device."""
import sys


class AudioBackend:
    def __init__(self):
        self.windows = sys.platform == 'win32'
        if self.windows:
            import pyaudiowpatch as pa
            self.api = pa
            self.audio = pa.PyAudio()
        elif sys.platform == 'darwin':
            import sounddevice as sd
            self.api = sd
            self.audio = None
        else:
            raise RuntimeError('Live capture supports Windows and macOS. File replay is platform independent.')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.audio is not None:
            self.audio.terminate()

    def devices(self):
        if self.windows:
            result = []
            for i in range(self.audio.get_device_count()):
                d = self.audio.get_device_info_by_index(i)
                if d['maxInputChannels']:
                    result.append(dict(index=i, name=d['name'], channels=int(d['maxInputChannels']),
                                       rate=int(d['defaultSampleRate']),
                                       host_api=self.audio.get_host_api_info_by_index(d['hostApi'])['name'],
                                       loopback=bool(d.get('isLoopbackDevice'))))
            return result
        return [dict(index=i, name=d['name'], channels=int(d['max_input_channels']),
                     rate=int(d['default_samplerate']), host_api='CoreAudio',
                     loopback='blackhole' in d['name'].lower())
                for i, d in enumerate(self.api.query_devices()) if d['max_input_channels']]

    def select(self, source, index=None):
        if index is None:
            if source == 'system':
                if not self.windows:
                    raise ValueError('macOS system audio requires --system-device INDEX: route output to BlackHole '
                                     'or another loopback input first. See README.md.')
                index = int(self.audio.get_default_wasapi_loopback()['index'])
            elif self.windows:
                try:
                    index = int(self.audio.get_host_api_info_by_type(self.api.paWASAPI)['defaultInputDevice'])
                    if index < 0:
                        raise ValueError('No default WASAPI input')
                except (OSError, ValueError):
                    index = int(self.audio.get_default_input_device_info()['index'])
            else:
                index = int(self.api.default.device[0])
        matches = [d for d in self.devices() if d['index'] == index]
        if not matches:
            raise ValueError(f'Input device {index} is unavailable. Run devices again.')
        device = matches[0]
        if self.windows and source == 'system' and not device['loopback']:
            raise ValueError('--system-device must select a WASAPI loopback input on Windows')
        if source == 'mic' and device['loopback']:
            raise ValueError('The selected microphone is a loopback device; use --mic-device for your physical microphone')
        return device

    def open(self, device, callback):
        if self.windows:
            def receive(data, frames, timing, status):
                callback(data, status)
                return (None, self.api.paContinue)
            raw = self.audio.open(format=self.api.paInt16, channels=min(2, device['channels']),
                                  rate=device['rate'], input=True, input_device_index=device['index'],
                                  frames_per_buffer=1024, stream_callback=receive, start=False)
            return WindowsStream(raw)
        def receive(data, frames, timing, status):
            callback(bytes(data), str(status) if status else None)
        raw = self.api.RawInputStream(samplerate=device['rate'], blocksize=1024,
                                     device=device['index'], channels=min(2, device['channels']),
                                     dtype='int16', callback=receive)
        return MacStream(raw)


class WindowsStream:
    def __init__(self, raw):
        self.raw = raw

    def start_stream(self):
        self.raw.start_stream()

    def is_active(self):
        return self.raw.is_active()

    def close(self):
        try:
            self.raw.stop_stream()
        finally:
            self.raw.close()


class MacStream:
    def __init__(self, raw):
        self.raw = raw

    def start_stream(self):
        self.raw.start()

    def is_active(self):
        return self.raw.active

    def close(self):
        try:
            self.raw.stop()
        finally:
            self.raw.close()
