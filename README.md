# ReMux

A small desktop app that puts a new audio track on a video, and optionally converts it to another format or codec. Runs on macOS, Windows and Linux.

- **Replace audio:** pick a video and an audio file (or a video to take audio from). Leave audio empty to keep the original.
- **Convert:** MP4, MOV, MKV, WebM, AVI or MPEG-TS, with stream copy (fast, lossless) or re-encoding to H.264, HEVC, AV1, VP9, ProRes, AAC, MP3, Opus, FLAC, ALAC, PCM or AC-3.
- **Make it smaller:** re-encode the video with a more efficient codec to save disk space. See [Making files smaller](#making-files-smaller).
- **Hardware encoding:** tick *Use graphics hardware* to encode on Apple's media engine, an NVIDIA or AMD graphics card, or Intel Quick Sync. It works for every codec and size that this computer's hardware supports, and only appears once ReMux has checked that the hardware works.
- **Show the result:** when it finishes, one click opens the output in Finder / Explorer / your file manager.

The output is saved next to the video as `<name>_remux.<ext>`, or in a folder you choose. If that file already exists, ReMux asks whether to replace it or keep both (saving `<name>_remux 2.<ext>`).

## Making files smaller

Under **Output → Change… → Size**:

| Size | Video becomes | What it does to the picture |
| --- | --- | --- |
| Keep original | unchanged | Nothing. The video is copied as it is. |
| Smaller (looks the same) | HEVC, 10-bit, constant quality (x265 CRF 21) | Most people won't see a difference on a TV or monitor. Very fine film grain and smooth dark gradients can come out a little softer. Plays on almost everything made since about 2017. |
| Smallest (slightly softer) | AV1, 10-bit, constant quality (SVT-AV1 CRF 30) with film-grain synthesis | Fine detail is slightly softened. Grain is removed and then recreated on playback, so it looks similar but isn't the original grain. Older TVs, phones and computers may not play AV1. |

### Fit a size I choose

Choose **Fit a size I choose…** and type the size you want, in MB or GB, for example to fit a USB stick or an upload limit. ReMux starts you at about half the original. Instead of aiming for a quality, the video gets an average bitrate worked out from that size, after leaving room for the sound. In software, ReMux reads the video twice: the first pass works out which scenes need the most data, and the second shares the space out to match. In testing, files came out within about 2% under the size asked for.

As you type, ReMux says what that size will look like:

| Rating | Roughly, for a 1080p film | What you'll see |
| --- | --- | --- |
| Excellent | 5 Mbps and up | Should look the same as the original. |
| Very good | about 3 Mbps | Hard to tell apart on most screens. |
| Good | about 1.75 Mbps | Fine detail and grain softened; clean on phones, laptops and most TVs. |
| Fair | about 1 Mbps | Noticeably soft, with some blocking in dark or fast scenes. |
| Poor | under 1 Mbps | Blurry and blocky. ReMux asks before making it. |

These ratings are a guide, not a promise. They're worked out from the bitrate per pixel, adjusted for resolution and for hardware encoding, and they can't see what's in the video: grainy, dark or fast-moving footage needs more space than animation or a talking head to look as good. ReMux won't start if the size isn't smaller than the original, or is too small even for the sound. Sizes are in decimal units (1 GB = 1,000 MB), as macOS shows them; Windows counts slightly differently, so 2 GB there is about 1.86 GB in Explorer.

Unlike *Smaller* and *Smallest*, a file fitted to a size is kept even if it ends up looking worse than you'd hoped, so check it before deleting the original.

How much space you save depends on the original. A Blu-ray rip or a camera file often shrinks to a half or a third of its size. A video that's already compressed, such as a streaming download or anything already in HEVC or AV1, barely shrinks and still loses a little detail. ReMux warns you about these, and if the new file isn't smaller than the original, it doesn't keep it.

Good to know:

- **Every re-encode loses something.** Constant-quality encoding keeps the loss small and spends more data on hard scenes (fast motion, darkness, grain) and less on easy ones, but it's never lossless. Keep the original if you might want to edit or re-encode it again.
- **Lossless audio is converted too.** Uncompressed, FLAC, ALAC, Dolby TrueHD and DTS-HD MA tracks can take up a third of a film's size. They're converted to AAC: 192 kb/s for stereo, 448 kb/s for 5.1 and 7.1. Most people can't hear the difference. To keep them, set *Audio codec* back to *Keep original*. Audio that's already compressed is copied as it is.
- **It's slow in software.** Expect it to take about as long as the video plays, or longer.
- **Graphics hardware is much faster but less efficient.** At the same quality, hardware encoders make larger files than x265 and SVT-AV1. ReMux sets their quality a little higher to make up for it, so *Smaller* still looks the same but saves less space. Apple's media engine has no AV1 encoder, so *Smallest* uses the software encoder on a Mac.

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
