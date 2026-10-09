# Product

<!-- impeccable:product-schema 1 -->

## Platform

desktop

Cross-platform desktop app (macOS, Windows, Linux) — not a web or mobile product. Platform conventions that matter are desktop ones: native file pickers, native dialogs, menu/keyboard behaviour, Finder / Explorer / file-manager reveal.

## Users

Non-technical creators who recorded or edited audio separately — a voiceover, a music bed, a cleaned-up soundtrack — and need it on their video. They don't know ffmpeg, don't want to open an editor for a one-step job, and may not know what a container or codec is. They arrive with two files and leave with one.

## Product Purpose

ReMux puts a new audio track on a video and, optionally, converts the result to another container or codec. Success is: pick a video, pick audio, press one button, get `<name>_remux.<ext>` next to the original, and open it with one click. Leaving audio empty turns it into a plain converter.

## Positioning

- **One job, one window.** Audio replacement plus rewrap/convert, nothing more. It is deliberately not a general-purpose transcoder like Handbrake.
- **Lossless by default.** Stream copy ("Copy (no re-encode)") is the default for both video and audio; re-encoding is an explicit choice.
- **Local, free, open source.** Files never leave the machine; no accounts, uploads or watermarks (unlike online converters).
- **Honest hardware options.** Hardware encoders (VideoToolbox, NVENC, Quick Sync, AMF) are listed only after they have been verified to work on this machine.

## Operating Context

- Launched from the desktop after an export from another tool (DAW, voice recorder, audio cleaner, screen recorder, camera).
- Inputs: one video file; one audio file, or a video to take audio from. Output is written beside the source video.
- Depends on a separately installed ffmpeg/ffprobe. When ffmpeg is missing, ReMux offers to install it via Homebrew (macOS) or winget (Windows); Linux users are pointed to their package manager.
- Distributed as unsigned builds (DMG, EXE, AppImage), so first launch involves Gatekeeper / SmartScreen / chmod steps documented in the README.

## Capabilities and Constraints

- Containers: MP4, MOV, MKV, WebM, AVI, MPEG-TS. Codec choices are filtered to what each container accepts.
- Video: copy, H.264, HEVC, AV1, VP9, ProRes 422 HQ (software and verified hardware variants). Audio: copy, AAC, MP3, Opus, Vorbis, FLAC, ALAC, PCM 24-bit, AC-3.
- Progress bar with cancel; overwrite confirmation; error output surfaced on failure; "Show in Finder/Explorer" after success.
- Current stack: single-file Python 3.9+ / Tkinter + ttk (`remux.py`), packaged with PyInstaller (`ReMux.spec`), built and smoke-tested per OS in GitHub Actions.
- UI framework is not locked: staying on stock Tkinter was not made a requirement, and a distinct custom look is acceptable. Any change must still build with PyInstaller on all three OSes.
- Terminology in the UI is technical by necessity (container, codec, stream copy). Non-technical users must still be able to succeed with the defaults.

## Brand Commitments

- Name: **ReMux**. App icon exists in `assets/icon.png`, `assets/icon.icns`, `assets/icon.ico`.
- Voice (from the README and UI copy): plain, short, direct, unhyped. Describes what happens ("The output is saved next to the video").
- Native feel on each OS is a commitment: platform file dialogs, alerts, keyboard behaviour and reveal-in-file-manager conventions must be respected, even if the window's visual identity becomes custom.

## Evidence on Hand

- README with feature list, download links and install notes.
- App icon assets in `assets/`.
- No screenshots, testimonials, user counts, reviews or press exist. Future work must not invent them.

## Product Principles

1. **Two files in, one file out.** The main path needs no knowledge of codecs; defaults must produce a correct, lossless result.
2. **Advanced options never get in the way.** Container and codec choices are available but secondary to the main action.
3. **Only offer what will work.** Don't list encoders, codecs or containers that will fail on this machine or with this combination.
4. **Explain failures in human terms.** Missing ffmpeg, overwrite risk and encode errors are explained in plain words with a next step.
5. **Respect the host OS.** Behave like a good citizen of macOS, Windows and Linux, wherever the visual style goes.

## Accessibility & Inclusion

Accessibility is a requirement: the whole flow must be operable by keyboard with visible focus, text and controls must have readable contrast in light and dark system appearances, and controls need meaningful accessible names for screen readers (VoiceOver, Narrator, Orca), within what Tk exposes. Known limitation to account for: Tk's screen-reader support is weak, which matters if the UI framework is reconsidered.
