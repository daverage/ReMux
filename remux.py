#!/usr/bin/env python3
"""ReMux: put a new audio track on a video, optionally changing format and codecs."""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from collections import namedtuple
from tkinter import filedialog, messagebox, ttk

VIDEO_PATTERNS = (
    "*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.wmv *.asf *.flv *.f4v *.mpg *.mpeg *.m2v "
    "*.vob *.ts *.mts *.m2ts *.3gp *.3g2 *.ogv *.mxf *.dv *.divx"
)
AUDIO_PATTERNS = (
    "*.mp3 *.m4a *.m4b *.aac *.wav *.aif *.aiff *.aifc *.caf *.flac *.ogg *.oga *.opus "
    "*.wma *.ac3 *.eac3 *.dts *.mka *.mp2 *.amr *.ape *.wv"
)
VIDEO_FILETYPES = (("Video files", VIDEO_PATTERNS), ("All files", "*"))
AUDIO_FILETYPES = (
    ("Audio files", AUDIO_PATTERNS),
    ("Video files (use their audio)", VIDEO_PATTERNS),
    ("All files", "*"),
)

COPY = "Copy (no re-encode)"

ALL_CODECS = {"h264", "hevc", "av1", "vp9", "prores", "aac", "mp3", "opus", "vorbis", "flac", "alac", "pcm", "ac3"}
# label: (extension, allowed codec families)
CONTAINERS = {
    "MP4": ("mp4", {"h264", "hevc", "av1", "vp9", "aac", "mp3", "opus", "flac", "alac", "ac3"}),
    "MOV": ("mov", {"h264", "hevc", "prores", "aac", "mp3", "alac", "pcm", "ac3"}),
    "MKV": ("mkv", ALL_CODECS),
    "WebM": ("webm", {"vp9", "av1", "opus", "vorbis"}),
    "AVI": ("avi", {"h264", "mp3", "ac3", "pcm"}),
    "MPEG-TS": ("ts", {"h264", "hevc", "aac", "mp3", "ac3", "opus"}),
}
EXT_TO_CONTAINER = {"m4v": "MP4", "mts": "MPEG-TS", "m2ts": "MPEG-TS"}
EXT_TO_CONTAINER.update({ext: label for label, (ext, _) in CONTAINERS.items()})

Encoder = namedtuple("Encoder", "label family name args")

# Constant-quality VideoToolbox encoding needs Apple Silicon; fall back to a bitrate elsewhere.
VT = ["-q:v", "65"] if platform.machine() == "arm64" else ["-b:v", "12M"]
NVENC = ["-preset", "p5", "-rc", "vbr", "-cq", "23"]
QSV = ["-global_quality", "23"]
AMF = ["-rc", "cqp", "-qp_i", "22", "-qp_p", "22"]
YUV420 = ["-pix_fmt", "yuv420p"]

SOFTWARE_VIDEO = [
    Encoder("H.264", "h264", "libx264", ["-crf", "20", "-preset", "medium", *YUV420]),
    Encoder("H.265 / HEVC", "hevc", "libx265", ["-crf", "22", "-preset", "medium"]),
    Encoder("AV1", "av1", "libsvtav1", ["-crf", "32"]),
    Encoder("AV1", "av1", "libaom-av1", ["-crf", "32", "-cpu-used", "6", "-row-mt", "1"]),
    Encoder("VP9", "vp9", "libvpx-vp9", ["-crf", "31", "-b:v", "0", "-row-mt", "1"]),
    Encoder("ProRes 422 HQ", "prores", "prores_ks", ["-profile:v", "hq"]),
]
HARDWARE_VIDEO = [
    Encoder("H.264 (VideoToolbox)", "h264", "h264_videotoolbox", [*VT, *YUV420]),
    Encoder("H.265 / HEVC (VideoToolbox)", "hevc", "hevc_videotoolbox", VT),
    Encoder("ProRes 422 HQ (VideoToolbox)", "prores", "prores_videotoolbox", ["-profile:v", "hq"]),
    Encoder("H.264 (NVENC)", "h264", "h264_nvenc", [*NVENC, *YUV420]),
    Encoder("H.265 / HEVC (NVENC)", "hevc", "hevc_nvenc", NVENC),
    Encoder("AV1 (NVENC)", "av1", "av1_nvenc", NVENC),
    Encoder("H.264 (Quick Sync)", "h264", "h264_qsv", QSV),
    Encoder("H.265 / HEVC (Quick Sync)", "hevc", "hevc_qsv", QSV),
    Encoder("AV1 (Quick Sync)", "av1", "av1_qsv", QSV),
    Encoder("H.264 (AMF)", "h264", "h264_amf", AMF),
    Encoder("H.265 / HEVC (AMF)", "hevc", "hevc_amf", AMF),
    Encoder("AV1 (AMF)", "av1", "av1_amf", AMF),
]
AUDIO = [
    Encoder("AAC", "aac", "aac", ["-b:a", "192k"]),
    Encoder("MP3", "mp3", "libmp3lame", ["-b:a", "192k"]),
    Encoder("Opus", "opus", "libopus", ["-b:a", "160k"]),
    Encoder("Vorbis", "vorbis", "libvorbis", ["-q:a", "5"]),
    Encoder("FLAC (lossless)", "flac", "flac", []),
    Encoder("ALAC (lossless)", "alac", "alac", []),
    Encoder("PCM 24-bit (uncompressed)", "pcm", "pcm_s24le", []),
    Encoder("AC-3 (Dolby Digital)", "ac3", "ac3", ["-b:a", "448k"]),
]

# Apps launched from the desktop get a minimal PATH, so also look where package managers install.
EXTRA_DIRS = {
    "darwin": ["/opt/homebrew/bin", "/usr/local/bin"],
    "win32": [os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")],
}.get(sys.platform, ["/usr/local/bin", "/snap/bin"])
# platform: (installer, arguments, display name)
INSTALLERS = {
    "darwin": ("brew", ["install", "ffmpeg"], "Homebrew"),
    "win32": ("winget", ["install", "-e", "--id", "Gyan.FFmpeg",
                         "--accept-source-agreements", "--accept-package-agreements"], "winget"),
}
MANUAL_INSTALL = {
    "darwin": "Install Homebrew from brew.sh, then run:\n  brew install ffmpeg",
    "win32": "Download it from https://ffmpeg.org/download.html and add its bin folder to PATH.",
}.get(sys.platform, "Install it with your package manager, for example:\n"
                    "  sudo apt install ffmpeg\n  sudo dnf install ffmpeg\n  sudo pacman -S ffmpeg")
ICON = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console popups on Windows
REVEAL_LABEL = {"darwin": "Show in Finder", "win32": "Show in Explorer"}.get(sys.platform, "Open Folder")


def find_tool(name):
    return shutil.which(name, path=os.pathsep.join([os.environ.get("PATH", ""), *EXTRA_DIRS]))


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW, **kwargs)


def detect_encoders(ffmpeg):
    """Return (video, audio) encoders this ffmpeg can use. Hardware encoders are test-run,
    since being compiled in doesn't mean the machine has the hardware."""
    listing = run([ffmpeg, "-hide_banner", "-encoders"]).stdout
    built_in = {line.split()[1] for line in listing.splitlines() if len(line.split()) > 1}

    def works(enc):
        test = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=640x360:d=0.1",
                "-frames:v", "1", "-c:v", enc.name, *enc.args, "-f", "null", "-"]
        try:
            return run(test, timeout=15).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    families = set()
    video = []
    for enc in SOFTWARE_VIDEO:  # first available encoder per family wins (e.g. SVT-AV1 over libaom)
        if enc.name in built_in and enc.family not in families:
            families.add(enc.family)
            video.append(enc)
    video += [enc for enc in HARDWARE_VIDEO if enc.name in built_in and works(enc)]
    audio = [enc for enc in AUDIO if enc.name in built_in]
    return video, audio


def media_duration(ffmpeg, path):
    """Duration in seconds, parsed from ffmpeg's input summary (avoids needing ffprobe)."""
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", run([ffmpeg, "-hide_banner", "-i", path]).stderr)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def reveal(path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


class ReMuxApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ReMux")
        if os.path.exists(ICON):
            self.iconphoto(True, tk.PhotoImage(file=ICON))
        if sys.platform.startswith("linux"):
            ttk.Style(self).theme_use("clam")  # Tk's default Linux theme looks dated
        self.resizable(True, False)
        self.minsize(520, 0)

        self.video_path = tk.StringVar()
        self.audio_path = tk.StringVar()
        self.container = tk.StringVar(value="MP4")
        self.vcodec = tk.StringVar(value=COPY)
        self.acodec = tk.StringVar(value=COPY)
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar()

        self.video_encoders = {}  # label -> Encoder
        self.audio_encoders = {}
        self.proc = None
        self.running = False
        self.cancelled = False
        self.last_output = None

        self._build_ui()
        self.container.trace_add("write", self._on_options_changed)
        self.video_path.trace_add("write", self._on_options_changed)
        self._on_options_changed()

        self.ffmpeg = find_tool("ffmpeg")
        if self.ffmpeg:
            self._detect_encoders()
        else:
            self.after(200, self._offer_ffmpeg_install)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        hint = {"foreground": "gray"}

        ttk.Label(root, text="Video").grid(row=0, column=0, sticky="w")
        ttk.Entry(root, textvariable=self.video_path, state="readonly").grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(root, text="Choose…", command=self._pick_video).grid(row=0, column=2)

        ttk.Label(root, text="Audio").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(root, textvariable=self.audio_path, state="readonly").grid(
            row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(root, text="Choose…", command=self._pick_audio).grid(row=1, column=2, pady=(8, 0))
        ttk.Label(root, text="Optional: leave empty to keep the video's own audio.", **hint).grid(
            row=2, column=1, sticky="w", padx=8)

        ttk.Separator(root).grid(row=3, column=0, columnspan=3, sticky="ew", pady=14)

        self._combo(root, 4, "Format", self.container, list(CONTAINERS))
        self.vbox = self._combo(root, 5, "Video codec", self.vcodec, [COPY])
        self.abox = self._combo(root, 6, "Audio codec", self.acodec, [COPY])

        ttk.Separator(root).grid(row=7, column=0, columnspan=3, sticky="ew", pady=14)

        ttk.Label(root, textvariable=self.output_var, **hint).grid(row=8, column=0, columnspan=3, sticky="w")
        self.progress = ttk.Progressbar(root, maximum=100)
        self.progress.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(root, textvariable=self.status_var, wraplength=480).grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=11, column=0, columnspan=3, sticky="e", pady=(12, 0))
        self.run_btn = ttk.Button(buttons, text="Remux", default="active", command=self._run_or_cancel)
        self.run_btn.pack(side="right")
        # Packed to the left of Remux only once there's a finished file to show.
        self.reveal_btn = ttk.Button(buttons, text=REVEAL_LABEL, command=lambda: reveal(self.last_output))
        self.bind("<Return>", lambda _: self._run_or_cancel())

    @staticmethod
    def _combo(parent, row, label, var, values):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        box = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=32)
        box.grid(row=row, column=1, sticky="w", padx=8, pady=2)
        return box

    def _pick_video(self):
        path = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_FILETYPES)
        if path:
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            self.container.set(EXT_TO_CONTAINER.get(ext, "MKV"))  # MKV accepts nearly anything
            self.video_path.set(path)

    def _pick_audio(self):
        path = filedialog.askopenfilename(title="Choose an audio track", filetypes=AUDIO_FILETYPES)
        if path:
            self.audio_path.set(path)

    def _on_options_changed(self, *_):
        """Offer only codecs the chosen format can hold, and preview the output path."""
        allowed = CONTAINERS[self.container.get()][1]
        for box, var, encoders in ((self.vbox, self.vcodec, self.video_encoders),
                                   (self.abox, self.acodec, self.audio_encoders)):
            labels = [COPY] + [label for label, enc in encoders.items() if enc.family in allowed]
            box.config(values=labels)
            if var.get() not in labels:
                var.set(COPY)
        output = self._output_path()
        self.output_var.set(f"Saves to: {output}" if output else "")

    def _output_path(self):
        video = self.video_path.get()
        if not video:
            return None
        ext = CONTAINERS[self.container.get()][0]
        return f"{os.path.splitext(video)[0]}_remux.{ext}"

    def _set_busy(self, busy, status=""):
        self.run_btn.config(text="Cancel" if busy else "Remux")
        self.reveal_btn.pack_forget()
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.status_var.set(status)

    # ── encoder detection / ffmpeg install ────────────────────────────────────

    def _detect_encoders(self):
        self.status_var.set("Checking available encoders…")

        def worker():
            video, audio = detect_encoders(self.ffmpeg)
            self.after(0, self._on_encoders_detected, video, audio)

        threading.Thread(target=worker, daemon=True).start()

    def _on_encoders_detected(self, video, audio):
        self.video_encoders = {enc.label: enc for enc in video}
        self.audio_encoders = {enc.label: enc for enc in audio}
        self._on_options_changed()
        if not self.running:
            self.status_var.set("")

    def _offer_ffmpeg_install(self):
        tool, args, name = INSTALLERS.get(sys.platform, (None, None, None))
        installer = tool and find_tool(tool)
        if not installer:
            messagebox.showerror("ffmpeg not found", f"ReMux needs ffmpeg.\n\n{MANUAL_INSTALL}")
        elif messagebox.askyesno("ffmpeg not found", f"ReMux needs ffmpeg.\n\nInstall it now with {name}?"):
            self.run_btn.config(state="disabled")
            self.progress.config(mode="indeterminate")
            self.progress.start(10)
            self.status_var.set(f"Installing ffmpeg with {name}… this can take a few minutes.")
            threading.Thread(target=self._install_ffmpeg, args=([installer, *args],), daemon=True).start()

    def _install_ffmpeg(self, cmd):
        result = run(cmd)
        self.after(0, self._on_install_done, result.returncode == 0, result.stderr or result.stdout)

    def _on_install_done(self, ok, log):
        self.run_btn.config(state="normal")
        self._set_busy(False)
        self.ffmpeg = find_tool("ffmpeg")
        if self.ffmpeg:
            self._detect_encoders()
        else:
            messagebox.showerror("Install failed", log[-2000:] if not ok else "The installer finished but ffmpeg wasn't found.\n"
                                 "Restart ReMux, or install ffmpeg manually.")

    # ── remux ─────────────────────────────────────────────────────────────────

    def _run_or_cancel(self):
        if self.running:
            self.cancelled = True
            if self.proc:
                self.proc.terminate()
            return
        if not self.ffmpeg:
            self._offer_ffmpeg_install()
            return
        if not self.video_path.get():
            messagebox.showwarning("No video", "Choose a video file first.")
            return

        output = self._output_path()
        if os.path.exists(output) and not messagebox.askyesno(
                "Replace file?", f"{os.path.basename(output)} already exists.\n\nReplace it?"):
            return

        self.running, self.cancelled = True, False
        self._set_busy(True, "Working…")
        threading.Thread(target=self._remux, args=(self._build_command(output), output), daemon=True).start()

    def _build_command(self, output):
        video, audio = self.video_path.get(), self.audio_path.get()
        venc = self.video_encoders.get(self.vcodec.get())
        aenc = self.audio_encoders.get(self.acodec.get())

        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats", "-progress", "pipe:1", "-y",
               "-i", video]
        if audio:
            cmd += ["-i", audio, "-map", "0:v:0", "-map", "1:a"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a?"]
        cmd += ["-c:v", venc.name, *venc.args] if venc else ["-c:v", "copy"]
        cmd += ["-c:a", aenc.name, *aenc.args] if aenc else ["-c:a", "copy"]
        if output.endswith((".mp4", ".mov")):
            cmd += ["-movflags", "+faststart"]
            if venc and venc.family == "hevc":
                cmd += ["-tag:v", "hvc1"]  # lets Apple players open HEVC
        return cmd + [output]

    def _remux(self, cmd, output):
        duration = media_duration(self.ffmpeg, self.video_path.get())
        if not duration:
            self.after(0, lambda: (self.progress.config(mode="indeterminate"), self.progress.start(10)))

        with tempfile.TemporaryFile(mode="w+", errors="replace") as log:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True, errors="replace",
                                         creationflags=NO_WINDOW)
            if self.cancelled:  # Cancel was pressed before ffmpeg started
                self.proc.terminate()
            for line in self.proc.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and duration and value.isdigit():
                    percent = min(100.0, int(value) / 1e6 / duration * 100)
                    self.after(0, lambda p=percent: (self.progress.config(value=p),
                                                     self.status_var.set(f"Working… {p:.0f}%")))
            code = self.proc.wait()
            log.seek(0)
            message = log.read().strip()
        self.after(0, self._on_finished, code, output, message)

    def _on_finished(self, code, output, message):
        self.proc, self.running = None, False
        self._set_busy(False)
        if code == 0:
            self.last_output = output
            self.progress.config(value=100)
            self.status_var.set(f"Done: saved {os.path.basename(output)}")
            self.reveal_btn.pack(side="right", padx=(0, 8))
            self.bell()
            return

        if os.path.exists(output):
            os.remove(output)  # don't leave a half-written file behind
        if self.cancelled:
            self.status_var.set("Cancelled.")
            return
        self.status_var.set("Remux failed.")
        if COPY in (self.vcodec.get(), self.acodec.get()):
            message += ("\n\nTip: a stream set to “Copy” may not be supported by the chosen format. "
                        "Pick a codec instead of Copy, or choose MKV.")
        messagebox.showerror("Remux failed", message[-2000:] or f"ffmpeg exited with code {code}.")


if __name__ == "__main__":
    ReMuxApp().mainloop()
