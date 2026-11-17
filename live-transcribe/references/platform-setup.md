# Platform setup

Run scripts/setup_local.py with Python 3.12; it creates .venv inside this skill folder and installs requirements.txt. Use .venv/Scripts/python.exe on Windows, .venv/bin/python on Mac. Run scripts/live_transcribe.py prepare-model --model base with that interpreter before recording.

Windows uses PyAudioWPatch/WASAPI. The default microphone prefers WASAPI. Check Windows microphone privacy permissions for desktop apps. Device indices can change; enumerate before selecting overrides.

macOS uses sounddevice/CoreAudio. Allow microphone access for the host app (Terminal/Codex) in System Settings > Privacy & Security > Microphone. If PortAudio cannot load, use Homebrew to install portaudio. Microphone-only recording needs no loopback driver.

For macOS system/both capture, separately install BlackHole 2ch from https://github.com/ExistentialAudio/BlackHole (Homebrew: brew install blackhole-2ch). Driver installation can require OS approval/restart. In Audio MIDI Setup create a Multi-Output Device including the listening output plus BlackHole. Follow https://github.com/ExistentialAudio/BlackHole/wiki/Multi-Output-Device for sample rates, clock source, and drift correction. Choose that output in the call app/system. Keep the call microphone on the physical microphone to avoid feeding the call back to itself. Enumerate inputs, then pass --system-device INDEX for BlackHole and --mic-device INDEX for the physical microphone. The multi-output playback index is not the input index. Do not change routing or install drivers without authorization. Restore normal playback after the call if desired.

Real Mac capture requires local validation; mocked tests do not establish routing or permissions. Hardware-free CI checks do not capture meetings.
