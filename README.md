# ReMux

A small desktop app that puts a new audio track on a video, and optionally converts it to another format or codec. Runs on macOS, Windows and Linux.

- **Replace audio:** pick a video and an audio file (or a video to take audio from). Leave audio empty to keep the original.
- **Convert:** MP4, MOV, MKV, WebM, AVI or MPEG-TS, with stream copy (fast, lossless) or re-encoding to H.264, HEVC, AV1, VP9, ProRes, AAC, MP3, Opus, FLAC, ALAC, PCM or AC-3.
- **Hardware encoding:** VideoToolbox (Mac), NVENC (NVIDIA), Quick Sync (Intel) and AMF (AMD) are offered when this computer actually supports them.
- **Show the result:** when it finishes, one click opens the output in Finder / Explorer / your file manager.

The output is saved next to the video as `<name>_remux.<ext>`, or in a folder you choose. If that file already exists, ReMux asks whether to replace it or keep both (saving `<name>_remux 2.<ext>`).

## Download

| Platform | Download |
| --- | --- |
| macOS (Apple Silicon) | [ReMux-macOS-arm64.dmg](https://github.com/daverage/ReMux/releases/latest/download/ReMux-macOS-arm64.dmg) |
| Windows x64 | [ReMux-Windows-x64.exe](https://github.com/daverage/ReMux/releases/latest/download/ReMux-Windows-x64.exe) |
| Linux x64 | [ReMux-Linux-x86_64.AppImage](https://github.com/daverage/ReMux/releases/latest/download/ReMux-Linux-x86_64.AppImage) |

The builds aren't code-signed:

- **macOS:** right-click ReMux.app → Open the first time (or run `xattr -dr com.apple.quarantine /Applications/ReMux.app`).
- **Windows:** in the SmartScreen prompt choose More info → Run anyway.
- **Linux:** `chmod +x ReMux-Linux-x86_64.AppImage`, then run it.

## Requirements

ReMux uses [ffmpeg](https://ffmpeg.org), which is installed separately. If it's missing, ReMux offers to install it with Homebrew (macOS) or winget (Windows). On Linux, use your package manager, e.g. `sudo apt install ffmpeg`.

## Run from source

Needs Python 3.9+ with Tkinter (on Debian/Ubuntu: `sudo apt install python3-tk`). To drag files into the window, also install `python3 -m pip install tkinterdnd2`; without it, use the buttons or the File menu.

```sh
python3 remux.py
```

## Build

```sh
python3 -m pip install pyinstaller tkinterdnd2
python3 -m PyInstaller ReMux.spec --noconfirm
```

This produces `dist/ReMux.app` on macOS, `dist/ReMux.exe` on Windows and `dist/ReMux` on Linux. Each platform must be built on that platform.

## Releases

[`.github/workflows/build.yml`](.github/workflows/build.yml) builds and smoke-tests all three platforms on every push. Pushing a version tag publishes a release with the installers:

```sh
git tag v1.0.0
git push origin v1.0.0
```

Keep `VERSION` in `ReMux.spec` in step with the tag.
