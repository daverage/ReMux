#!/usr/bin/env python3
"""ReMux: put a new audio track on a video, optionally changing format and codecs."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, font, messagebox, ttk

try:  # optional: without it, files still come in through the buttons, the menu, and Open With on macOS
    import tkinterdnd2
except ImportError:
    tkinterdnd2 = None

VIDEO_PATTERNS = (
    "*.mp4 *.m4v *.mov *.mkv *.webm *.avi *.wmv *.asf *.flv *.f4v *.mpg *.mpeg *.m2v "
    "*.vob *.ts *.mts *.m2ts *.3gp *.3g2 *.ogv *.mxf *.dv *.divx"
)
AUDIO_PATTERNS = (
    "*.mp3 *.m4a *.m4b *.aac *.wav *.aif *.aiff *.aifc *.caf *.flac *.ogg *.oga *.opus "
    "*.wma *.ac3 *.eac3 *.dts *.mka *.mp2 *.amr *.ape *.wv"
)
VIDEO_EXTS = {pattern[2:] for pattern in VIDEO_PATTERNS.split()}
AUDIO_EXTS = {pattern[2:] for pattern in AUDIO_PATTERNS.split()}
VIDEO_FILETYPES = (("Video files", VIDEO_PATTERNS), ("All files", "*"))
AUDIO_FILETYPES = (
    ("Audio files", AUDIO_PATTERNS),
    ("Video files (use their audio)", VIDEO_PATTERNS),
    ("All files", "*"),
)

COPY = "Keep original (lossless)"

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
Media = namedtuple("Media", "duration video audio")

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
AUDIO_NAMES = {"pcm": "Uncompressed (PCM)", "aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis",
               "flac": "FLAC", "alac": "ALAC", "ac3": "AC-3"}
LOSSLESS = {"pcm", "flac", "alac"}
VIDEO_FAMILIES = {"h264", "hevc", "av1", "vp9", "prores"}  # ffmpeg names that match the CONTAINERS families
VIDEO_NAMES = {"h264": "H.264", "hevc": "HEVC", "av1": "AV1", "vp9": "VP9", "vp8": "VP8", "prores": "ProRes",
               "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "dvvideo": "DV", "mjpeg": "Motion JPEG"}
# What to convert audio to when it can't be copied into a format, best first. MKV holds anything.
FALLBACK_AUDIO = {"MP4": ["aac"], "MOV": ["aac"], "WebM": ["opus", "vorbis"], "AVI": ["mp3", "ac3"],
                  "MPEG-TS": ["aac", "mp3"]}
CONFIRM_CANCEL_AFTER = 10  # seconds; shorter jobs stop without asking

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
    "darwin": "Install Homebrew from brew.sh, then run this in Terminal:\n  brew install ffmpeg",
    "win32": "Download it from https://ffmpeg.org/download.html and add its bin folder to PATH.",
}.get(sys.platform, "Install it with your package manager, for example:\n"
                    "  sudo apt install ffmpeg\n  sudo dnf install ffmpeg\n  sudo pacman -S ffmpeg")
# ffmpeg error text -> what to tell the user. First match wins; {video}/{audio} are file names.
FFMPEG_ERRORS = [
    (r"No space left on device", "The disk is full. Free up some space and try again."),
    (r"Permission denied|Operation not permitted", "ReMux isn't allowed to write to that folder. "
                                                   "Move the video somewhere you can save files, such as Documents."),
    (r"Stream map '0:v:0' matches no streams", "{video} has no video track. Choose a video file."),
    (r"Unknown encoder|Error while opening encoder|Could not open encoder|Error initializing output stream",
     "The chosen codec couldn't start on this computer. Choose a different video or audio codec."),
    (r"not currently supported in container|Could not find tag for codec|Only .* supported|"
     r"codec not supported|Could not write header",
     "The chosen format can't hold this video or audio as it is. "
     "Choose a codec other than “" + COPY + "”, or switch the format to MKV, which holds almost anything."),
    (r"Invalid data found|moov atom not found|could not find codec parameters",
     "One of the files couldn't be read. It may be damaged, still being exported, or not a video or audio file."),
]
ICON = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console popups on Windows
HELP_URL = "https://github.com/daverage/ReMux#readme"
# Secondary text, chosen for at least 4.5:1 against every window background ReMux runs on: Aqua #ECECEC,
# clam #DCDAD5 and Windows #F0F0F0 in light; Aqua #323232 and the dark palette below in dark.
SECONDARY_LIGHT = "#5e5e5e"
SECONDARY_DARK = "#a3a3a3"
# Notes that change what will happen (a conversion, a fit problem), at 4.5:1 or more in each appearance.
NOTE_LIGHT = "#8a4b00"
NOTE_DARK = "#f0b04a"
# Windows and Linux themes have no dark mode of their own, so ReMux supplies one on top of clam.
DARK = {"bg": "#2b2b2b", "field": "#1f1f1f", "button": "#3a3a3a", "hover": "#454545", "fg": "#e8e8e8",
        "border": "#4a4a4a", "accent": "#2f6bc8", "disabled": "#8c8c8c"}
NEEDS_FFMPEG = "ReMux uses ffmpeg, a free video tool, to do its work, and it isn't installed yet."
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
    # Each test can take seconds while a driver wakes up, so run them side by side rather than one after another.
    candidates = [enc for enc in HARDWARE_VIDEO if enc.name in built_in]
    if candidates:
        with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
            video += [enc for enc, ok in zip(candidates, pool.map(works, candidates)) if ok]
    audio = [enc for enc in AUDIO if enc.name in built_in]
    return video, audio


def media_info(ffmpeg, path):
    """Length in seconds and first video/audio codec, parsed from ffmpeg's input summary (avoids needing ffprobe)."""
    err = run([ffmpeg, "-hide_banner", "-i", path]).stderr
    duration = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", err)
    video = re.search(r"Stream #\S+.*?: Video: (\w+)(?!.*attached pic)", err)  # cover art isn't video
    audio = re.search(r"Stream #\S+.*?: Audio: (\w+)", err)
    if duration:
        h, m, s = duration.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    return Media(duration or None, video and video.group(1), audio and audio.group(1))


def clock(seconds):
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def span(seconds):
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


def short_folder(path, limit=44):
    """The file's folder, with home as ~ and, when long, middle folders dropped whole: ~/…/Projects/Episode 14."""
    folder = os.path.dirname(path)
    home = os.path.expanduser("~")
    if folder == home or folder.startswith(home + os.sep):
        folder = "~" + folder[len(home):]
    if len(folder) <= limit:
        return folder
    parts = folder.split(os.sep)
    tail = []
    for part in reversed(parts[1:]):
        if tail and len(parts[0]) + len(os.sep.join([part, *tail])) + 4 > limit:
            break
        tail.insert(0, part)
    return os.sep.join([parts[0], "…", *tail])


def time_left(seconds):
    if seconds < 60:
        return "less than a minute left"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} min left"
    hours, minutes = divmod(minutes, 60)
    return f"about {hours} h {minutes} min left" if minutes else f"about {hours} h left"


def settings_path():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "ReMux", "settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            settings = json.load(f)
        return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    """Best effort: remembering folders is a convenience, so a failed write is never an error."""
    path = settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def codec_family(codec):
    if codec.startswith("pcm_"):
        return "pcm"
    return codec if codec in AUDIO_NAMES else None


def tail(log, lines=6):
    """The last few lines of a log: enough to search for or report, without a wall of text."""
    return "\n".join(log.strip().splitlines()[-lines:])


def numbered(path):
    """The first free "name 2.ext", "name 3.ext", … next to path, the way Finder and Explorer keep both files."""
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} {n}{ext}"):
        n += 1
    return f"{stem} {n}{ext}"


def system_is_dark(root):
    if sys.platform == "darwin":
        try:
            return bool(int(root.tk.call("::tk::unsupported::MacWindowStyle", "isdark", root)))
        except (tk.TclError, ValueError):
            return False
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False
    try:  # GNOME and the desktops that follow its setting; older ones only name a "-dark" theme
        scheme = run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]).stdout
        theme = run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"]).stdout
        return "dark" in scheme or "-dark" in theme.lower()
    except OSError:
        return False


def notify(title, message):
    """Post a desktop notification. Returns False where there's no simple native way to."""
    try:
        if sys.platform == "darwin":
            quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
            subprocess.Popen(["osascript", "-e", f"display notification {quote(message)} with title {quote(title)}"])
            return True
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "--app-name=ReMux", title, message])
            return True
    except OSError:
        pass
    return False


def enable_dpi_awareness():
    """Without this, Windows scales the window up as a bitmap on high-DPI displays and the text comes out blurry."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7 and 8
        except (AttributeError, OSError):
            pass


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
        self._apply_theme()
        self.resizable(True, False)
        self.minsize(520, 0)

        self.video_path = tk.StringVar()
        self.audio_path = tk.StringVar()
        self.container = tk.StringVar(value="MP4")
        self.vcodec = tk.StringVar(value=COPY)
        self.acodec = tk.StringVar(value=COPY)
        self.video_name = tk.StringVar()  # file names shown in the fields; the *_path vars hold full paths
        self.audio_name = tk.StringVar()
        self.video_details = tk.StringVar()
        self.audio_details = tk.StringVar()
        self.save_name = tk.StringVar()
        self.save_details = tk.StringVar()
        self.status_var = tk.StringVar()
        self.audio_note = tk.StringVar()
        self.output_summary = tk.StringVar()
        self.options_open = False
        # Folders are remembered between launches: where videos and audio were last picked, and the output folder.
        self.settings = load_settings()
        saved_dir = self.settings.get("output_dir")
        self.output_dir = saved_dir if saved_dir and os.path.isdir(saved_dir) else None  # None: next to the video
        self.just_saved = False  # until the inputs change, the window describes the finished job

        self.video_encoders = {}  # label -> Encoder
        self.audio_encoders = {}
        self.media = {}  # path -> Media, or None while it's being read
        self.audio_info = None  # (path, codec or None) for the audio the output will use
        self.auto_acodec = None  # audio codec ReMux picked because Copy wouldn't work
        self.proc = None
        self.running = False
        self.cancelled = False
        self.installing = False
        self.started_at = 0.0
        self.job_output = None
        self.tmp_output = None
        self.last_output = None

        self._build_ui()
        self._build_menu()
        self.can_drop = self._enable_drop()
        self.container.trace_add("write", self._on_options_changed)
        self.video_path.trace_add("write", self._on_options_changed)
        self.video_path.trace_add("write", self._probe_files)
        self.audio_path.trace_add("write", self._probe_files)
        self.acodec.trace_add("write", lambda *_: self._check_fit())
        self.vcodec.trace_add("write", lambda *_: self._check_fit())
        self.audio_path.trace_add("write", lambda *_: self.running or self.run_btn.config(text=self._action()))
        self._on_options_changed()
        self._show_file_details()
        for var in (self.video_path, self.audio_path, self.container, self.vcodec, self.acodec):
            var.trace_add("write", lambda *_: setattr(self, "just_saved", False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.ffmpeg = find_tool("ffmpeg")
        if self.ffmpeg:
            self._detect_encoders()
        else:
            self.after(200, self._offer_ffmpeg_install)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Spacing steps: 4 within a group, 12 between related groups, 16 around separators, 20 at the window edge.
        self.root_frame = root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        hint = {"style": "Secondary.TLabel"}
        # File names lead each row; labels, details and folders support them.
        self.name_font = font.nametofont("TkDefaultFont").copy()
        self.name_font.configure(weight="bold")

        # The two files: the job itself. Each field shows the file name, with where it is and what's in it below.
        ttk.Label(root, text="Video").grid(row=0, column=0, sticky="w")
        # The fields only display a name, so Tab skips them and goes straight to the buttons.
        self.video_entry = ttk.Entry(root, textvariable=self.video_name, state="readonly", width=36, takefocus=0,
                                     font=self.name_font, cursor="hand2")
        self.video_entry.grid(row=0, column=1, sticky="ew", padx=8)
        self.video_btn = ttk.Button(root, text="Choose Video…", command=self._pick_video)
        self.video_btn.grid(row=0, column=2, sticky="ew")
        self.video_details_label = ttk.Label(root, textvariable=self.video_details, **hint)
        self.video_details_label.grid(row=1, column=1, sticky="w", padx=8, pady=(4, 0))

        ttk.Label(root, text="Audio").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.audio_entry = ttk.Entry(root, textvariable=self.audio_name, state="readonly", takefocus=0,
                                     font=self.name_font, cursor="hand2")
        self.audio_entry.grid(
            row=2, column=1, sticky="ew", padx=8, pady=(12, 0))
        self.audio_btn = ttk.Button(root, text="Choose Audio…", command=self._pick_audio)
        self.audio_btn.grid(
            row=2, column=2, sticky="ew", pady=(12, 0))
        self.audio_details_label = ttk.Label(root, textvariable=self.audio_details, **hint)
        self.audio_details_label.grid(row=3, column=1, sticky="nw", padx=8, pady=(4, 0))
        self.remove_btn = ttk.Button(root, text="Remove Audio", command=lambda: self.audio_path.set(""))
        self.remove_btn.grid(row=3, column=2, sticky="new", pady=(4, 0))
        self.remove_btn.grid_remove()

        ttk.Separator(root).grid(row=4, column=0, columnspan=3, sticky="ew", pady=16)

        # Output settings: the defaults are right for most people, so they read as one line until asked for.
        ttk.Label(root, text="Output").grid(row=5, column=0, sticky="w")
        self.summary_label = ttk.Label(root, textvariable=self.output_summary)
        self.summary_label.grid(row=5, column=1, sticky="w", padx=8)
        self.options_btn = ttk.Button(root, text="Change…", command=self._toggle_options)
        self.options_btn.grid(row=5, column=2, sticky="ew")
        self.option_rows = [
            self._combo(root, 6, "Format", self.container, list(CONTAINERS), pady=(12, 2)),
            self._combo(root, 7, "Video codec", self.vcodec, [COPY]),
            self._combo(root, 8, "Audio codec", self.acodec, [COPY]),
        ]
        self.vbox, self.abox = self.option_rows[1][1], self.option_rows[2][1]
        # Size the label column for the hidden option labels too, so opening them doesn't widen the window.
        root.columnconfigure(0, minsize=max(text.winfo_reqwidth() for text, _ in self.option_rows))
        for widgets in self.option_rows:
            for widget in widgets:
                widget.grid_remove()
        # Shown, open or not, when the video or audio can't simply be copied into the chosen format.
        self.note_label = ttk.Label(root, textvariable=self.audio_note, style="Note.TLabel")
        self.note_label.grid(row=9, column=1, sticky="w", padx=8, pady=(4, 0))
        # The note's suggestion as a button, so it's one click rather than a trip into the options.
        self.note_btn = ttk.Button(root, command=lambda: self.container.set(self.note_action))
        self.note_btn.grid(row=9, column=2, sticky="new", pady=(4, 0))
        self.note_btn.grid_remove()
        self.note_action = None
        self.note_label.grid_remove()

        ttk.Separator(root).grid(row=10, column=0, columnspan=3, sticky="ew", pady=16)

        # Where the new file goes: its name, then the folder, matching the file rows above.
        ttk.Label(root, text="Save as").grid(row=11, column=0, sticky="w")
        self.save_name_label = ttk.Label(root, textvariable=self.save_name, font=self.name_font)
        self.save_name_label.grid(row=11, column=1, sticky="w", padx=8)
        self.folder_btn = ttk.Button(root, text="Choose Folder…", command=self._pick_folder)
        self.folder_btn.grid(row=11, column=2, sticky="ew")
        self.save_details_label = ttk.Label(root, textvariable=self.save_details, **hint)
        self.save_details_label.grid(row=12, column=1, sticky="nw", padx=8, pady=(4, 0))
        self.same_folder_btn = ttk.Button(root, text="Use Video's Folder", command=self._use_video_folder)
        self.same_folder_btn.grid(row=12, column=2, sticky="new", pady=(4, 0))
        self.same_folder_btn.grid_remove()

        ttk.Separator(root).grid(row=13, column=0, columnspan=3, sticky="ew", pady=(16, 0))

        # Footer: what's happening on the left, actions on the right. The bar appears only while something runs.
        footer = ttk.Frame(root)
        footer.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status_var)
        self.status_label.grid(row=0, column=0, sticky="sw")
        self.progress = ttk.Progressbar(footer, maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        # Keep the bar's row even while it's hidden, so starting a job doesn't make the window jump taller.
        footer.rowconfigure(1, minsize=self.progress.winfo_reqheight() + 4)
        self.progress.grid_remove()
        # Shown left of the main button only once there's a finished file to show.
        self.reveal_btn = ttk.Button(footer, text=REVEAL_LABEL, command=lambda: reveal(self.last_output))
        self.reveal_btn.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 8))
        self.reveal_btn.grid_remove()
        self.run_btn = ttk.Button(footer, text="Convert", default="active", command=self._run_or_cancel,
                                  style="Primary.TButton")
        self.run_btn.grid(row=0, column=2, rowspan=2, sticky="e")

        # Text wraps to the width of the file fields, so long names and messages never widen the window.
        self.video_entry.bind("<Configure>", self._rewrap)
        root.bind("<Configure>", self._rewrap)
        # The fields only show a name, so clicking one does what the user expects: choose that file.
        self.video_entry.bind("<Button-1>", lambda _: self._pick_video())
        self.audio_entry.bind("<Button-1>", lambda _: self._pick_audio())
        self.bind("<Return>", self._on_return)
        self.bind("<Escape>", lambda _: self._cancel())

    def _rewrap(self, _=None):
        # A little under the field's width: wrapping at exactly its width lets label padding widen the column,
        # which re-triggers this and creeps the window wider.
        field = max(self.video_entry.winfo_width() - 12, 200)
        for label in (self.video_details_label, self.audio_details_label, self.note_label, self.summary_label,
                      self.save_name_label, self.save_details_label, self.status_label):
            label.config(wraplength=field)

    def _build_menu(self):
        mac = sys.platform == "darwin"
        key = "Command" if mac else "Control"
        # Tk draws "Command-O" as ⌘O on macOS; elsewhere the label is shown as written.
        shown = (lambda keys: keys) if mac else (
            lambda keys: keys.replace("Shift-Control-", "Ctrl+Shift+").replace("Control-", "Ctrl+"))
        menubar = tk.Menu(self)
        if mac:
            # Replaces Tk's own app menu, which offers "About Tcl & Tk".
            app_menu = tk.Menu(menubar, name="apple", tearoff=False)
            app_menu.add_command(label="About ReMux", command=self._about)
            menubar.add_cascade(menu=app_menu)
            self.createcommand("tk::mac::ShowAbout", self._about)
            self.createcommand("tk::mac::Quit", self._on_close)  # ⌘Q asks first if a job is running
            self.createcommand("::tk::mac::OpenDocument", lambda *paths: self._open_files(paths))
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Choose Video…", accelerator=shown(f"{key}-O"), command=self._pick_video)
        file_menu.add_command(label="Choose Audio…", accelerator=shown(f"Shift-{key}-O"), command=self._pick_audio)
        file_menu.add_command(label="Choose Output Folder…", command=self._pick_folder)
        file_menu.add_separator()
        if mac:
            file_menu.add_command(label="Close Window", accelerator="Command-W", command=self._on_close)
        else:
            file_menu.add_command(label="Quit", accelerator="Ctrl+Q", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menubar, name="help", tearoff=False)
        help_menu.add_command(label="ReMux Help", command=lambda: webbrowser.open(HELP_URL))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

        self.bind_all(f"<{key}-o>", lambda _: self._pick_video())
        self.bind_all(f"<{key}-O>", lambda _: self._pick_audio())  # Shift turns o into O
        self.bind_all("<Command-w>" if mac else "<Control-q>", lambda _: self._on_close())

    def _about(self):
        messagebox.showinfo("About ReMux", "ReMux",
                            detail="Puts a new audio track on a video, or converts it to another format, "
                                   f"using ffmpeg.\n\n{HELP_URL.split('#')[0]}")

    def _apply_theme(self, *_):
        """Match the system's light or dark appearance. macOS reports changes as they happen; Windows and Linux
        are read at launch."""
        dark = system_is_dark(self)
        style = ttk.Style(self)
        style.configure("Secondary.TLabel", foreground=SECONDARY_DARK if dark else SECONDARY_LIGHT)
        style.configure("Note.TLabel", foreground=NOTE_DARK if dark else NOTE_LIGHT)
        if dark and sys.platform != "darwin":
            p = DARK
            style.theme_use("clam")
            self.configure(background=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                            bordercolor=p["border"], lightcolor=p["button"], darkcolor=p["button"],
                            troughcolor=p["field"], selectbackground=p["accent"], selectforeground="#ffffff",
                            insertcolor=p["fg"], arrowcolor=p["fg"], focuscolor=p["accent"])
            style.map(".", foreground=[("disabled", p["disabled"])])
            style.configure("TButton", background=p["button"])
            style.map("TButton", background=[("pressed", p["border"]), ("active", p["hover"])])
            style.configure("TProgressbar", background=p["accent"])
            # clam marks the default button only with a border; give the main action the accent, as Aqua does.
            style.configure("Primary.TButton", background=p["accent"], foreground="#ffffff",
                            bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"])
            # Both stay at 4.5:1 or more behind white text.
            style.map("Primary.TButton", background=[("pressed", "#2a5fb3"), ("active", "#3570cc")])
            for widget in ("TEntry", "TCombobox"):
                style.map(widget, fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])],
                          selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])])
            self.option_add("*TCombobox*Listbox.background", p["field"])
            self.option_add("*TCombobox*Listbox.foreground", p["fg"])
            self.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
            self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        if sys.platform == "darwin" and not getattr(self, "_watching_appearance", False):
            self._watching_appearance = True
            for event in ("<<LightAqua>>", "<<DarkAqua>>", "<Activate>"):  # <Activate> catches older Tk versions
                self.bind(event, self._apply_theme, add="+")

    @staticmethod
    def _combo(parent, row, label, var, values, pady=2):
        text = ttk.Label(parent, text=label)
        text.grid(row=row, column=0, sticky="w", pady=pady)
        box = ttk.Combobox(parent, textvariable=var, values=values, state="readonly")
        box.grid(row=row, column=1, sticky="ew", padx=8, pady=pady)
        return text, box

    def _toggle_options(self):
        self.options_open = not self.options_open
        for widgets in self.option_rows:
            for widget in widgets:
                widget.grid() if self.options_open else widget.grid_remove()
        self.options_btn.config(text="Hide Options" if self.options_open else "Change…")
        if self.options_open:
            self.option_rows[0][1].focus_set()

    def _summarize(self):
        video = "original video" if self.vcodec.get() == COPY else f"video as {self.vcodec.get()}"
        audio = "original audio" if self.acodec.get() == COPY else f"audio as {self.acodec.get()}"
        if self.vcodec.get() == self.acodec.get() == COPY:
            body = "original video and audio, no quality loss"
        else:
            body = f"{video}, {audio}"
        self.output_summary.set(f"{self.container.get()} · {body}")

    def _pick_video(self):
        if self.running:
            return
        path = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_FILETYPES,
                                          initialdir=self._remembered("video_dir"))
        if path:
            self._set_video(path)

    def _set_video(self, path):
        self.media.pop(path, None)
        self._remember("video_dir", os.path.dirname(path))
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        container = EXT_TO_CONTAINER.get(ext, "MKV")  # MKV accepts nearly anything
        self.container.set(container)
        self.video_path.set(path)
        same = EXT_TO_CONTAINER.get(ext) == container
        self.status_var.set(f"Format set to {container} to match your video." if same else
                            "Format set to MKV, which can hold this video.")

    def _pick_audio(self):
        if self.running:
            return
        path = filedialog.askopenfilename(title="Choose an audio track", filetypes=AUDIO_FILETYPES,
                                          initialdir=self._remembered("audio_dir"))
        if path:
            self._set_audio(path)

    def _set_audio(self, path):
        if path == self.video_path.get():
            self.status_var.set(f"{os.path.basename(path)} is already the video. Choose a different audio file.")
            return
        self.media.pop(path, None)
        self._remember("audio_dir", os.path.dirname(path))
        self.audio_path.set(path)

    def _remembered(self, key):
        folder = self.settings.get(key)
        return folder if folder and os.path.isdir(folder) else None

    def _remember(self, key, value):
        if self.settings.get(key) != value:
            self.settings[key] = value
            save_settings(self.settings)

    def _enable_drop(self):
        """Let files be dragged into the window: onto a row to fill it, or anywhere to be sorted by type."""
        if not tkinterdnd2:
            return False
        try:
            tkinterdnd2.TkinterDnD._require(self)
        except (RuntimeError, tk.TclError):
            return False  # no tkdnd build for this platform
        targets = [(self.root_frame, None), (self.video_entry, "video"), (self.video_details_label, "video"),
                   (self.audio_entry, "audio"), (self.audio_details_label, "audio")]
        for widget, role in targets:
            widget.drop_target_register(tkinterdnd2.DND_FILES)
            widget.dnd_bind("<<Drop>>", lambda event, role=role: self._on_drop(event, role))
        return True

    def _on_drop(self, event, role):
        self._open_files(self.tk.splitlist(event.data), role)
        return event.action

    def _open_files(self, paths, role=None):
        """Files from a drop or from Finder. A row takes the first file; otherwise audio files go to Audio, and
        a video fills Video, or becomes the audio when it's the second of two files."""
        if self.running:
            return
        paths = [path for path in paths if os.path.isfile(path)]
        if not paths:
            return
        kind = lambda path: os.path.splitext(path)[1].lstrip(".").lower()
        name = os.path.basename(paths[0])
        if role == "video":
            if kind(paths[0]) in AUDIO_EXTS:  # an audio file can't be the video; it was meant for Audio
                self._set_audio(paths[0])
                self.status_var.set(f"{name} is an audio file, so it's been used as the audio.")
                return
            return self._set_video(paths[0])
        if role == "audio":
            if paths[0] == self.video_path.get():
                return self._set_audio(paths[0])  # refuses, and says why
            self._set_audio(paths[0])
            if kind(paths[0]) not in AUDIO_EXTS:  # a video here is allowed: its sound replaces the video's
                self.status_var.set(f"Using the audio from {name}.")
            return
        audio = [path for path in paths if kind(path) in AUDIO_EXTS]
        video = [path for path in paths if path not in audio]
        if video:
            self._set_video(video[0])
        if audio or len(video) > 1:
            self._set_audio(audio[0] if audio else video[1])

    def _on_options_changed(self, *_):
        """Offer only codecs the chosen format can hold, and preview the output path."""
        allowed = CONTAINERS[self.container.get()][1]
        for box, var, encoders in ((self.vbox, self.vcodec, self.video_encoders),
                                   (self.abox, self.acodec, self.audio_encoders)):
            labels = [COPY] + [label for label, enc in encoders.items() if enc.family in allowed]
            box.config(values=labels)
            if var.get() not in labels:
                if var.get() not in (COPY, self.auto_acodec) and not self.running:
                    kind = "Video" if var is self.vcodec else "Audio"
                    self.status_var.set(f"{self.container.get()} can't hold {var.get()}, "
                                        f"so {kind.lower()} is back to “{COPY}”.")
                var.set(COPY)
        self._show_output()
        self._check_fit(adjust=True)

    def _show_output(self):
        output = self._output_path()
        where = f"In {short_folder(os.path.join(self.output_dir, 'x'))}" if self.output_dir else None
        self.same_folder_btn.grid() if self.output_dir else self.same_folder_btn.grid_remove()
        # Bold is for file names; with no video yet the row only says where the file will go.
        self.save_name_label.config(font=self.name_font if output else "TkDefaultFont")
        if not output:
            self.save_name.set(where or "Next to the video")
            self.save_details.set("")
            self.save_details_label.grid_remove()
            return
        details = where or "Next to the video"  # its folder is already shown under Video
        if self.just_saved:
            details += "\nRunning again will ask whether to replace it or keep both."
        elif os.path.exists(output):
            details += "\nA file with this name is already there. You'll be asked whether to replace it or keep both."
        self.save_name.set(os.path.basename(output))
        self.save_details.set(details)
        self.save_details_label.grid()

    def _pick_folder(self):
        if self.running:
            return
        video = self.video_path.get()
        folder = filedialog.askdirectory(title="Choose where to save the new file", mustexist=True,
                                         initialdir=self.output_dir or (video and os.path.dirname(video)) or None)
        if folder:
            same = video and os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.dirname(video))
            self.output_dir = None if same else folder
            self._remember("output_dir", self.output_dir)
            self.just_saved = False
            self._show_output()

    def _use_video_folder(self):
        if self.running:
            return
        self.output_dir = None
        self._remember("output_dir", None)
        self.just_saved = False
        self._show_output()

    def _probe_files(self, *_):
        """Read each picked file's length and codecs once, to describe it and to catch audio that won't fit."""
        for path in (self.video_path.get(), self.audio_path.get()):
            if path and path not in self.media and self.ffmpeg:
                self.media[path] = None  # being read
                threading.Thread(target=self._probe_worker, args=(path,), daemon=True).start()
        self._on_probed()

    def _probe_worker(self, path):
        info = media_info(self.ffmpeg, path)
        self.after(0, self._on_probed, path, info)

    def _on_probed(self, path=None, info=None):
        if path:
            self.media[path] = info
        source = self.audio_path.get() or self.video_path.get()
        known = self.media.get(source)
        self.audio_info = (source, known.audio) if known else None
        self._check_fit(adjust=True)
        self._show_file_details()

    def _show_file_details(self):
        video, audio = self.video_path.get(), self.audio_path.get()
        self.video_name.set(os.path.basename(video))
        self.audio_name.set(os.path.basename(audio))
        v, a = self.media.get(video), self.media.get(audio)

        if not video:
            self.video_details.set("The video to put new audio on, or to convert."
                                   + (" Drop it here, or click to choose." if self.can_drop else ""))
        elif v:
            streams = f"{VIDEO_NAMES.get(v.video, v.video.upper())} video" if v.video else "no video"
            streams += f", {self._audio_name(v.audio)} audio" if v.audio else ", no audio"
            facts = " · ".join(filter(None, [v.duration and clock(v.duration), streams]))
            self.video_details.set(f"{facts}\nIn {short_folder(video)}")
        else:
            self.video_details.set(f"Reading…\nIn {short_folder(video)}")

        if not audio:
            self.audio_details.set("Optional: leave empty to keep the video's own audio.")
            self.remove_btn.grid_remove()
            return
        self.remove_btn.grid()
        parts = []
        if a:
            parts += [a.duration and clock(a.duration), a.audio and f"{self._audio_name(a.audio)} audio"]
            # Length is the thing most likely to be wrong with a separately made track, so compare it.
            if a.duration and v and v.duration and abs(a.duration - v.duration) >= 1:
                diff = span(abs(a.duration - v.duration))
                parts.append(f"{diff} shorter than the video, so the end will be silent" if a.duration < v.duration
                             else f"{diff} longer than the video, so the end will be cut")
        facts = " · ".join(filter(None, parts)) or "Reading…"
        # The folder only when it differs from the video's, which is shown just above.
        same_place = os.path.dirname(audio) == os.path.dirname(video)
        self.audio_details.set(facts if same_place else f"{facts}\nIn {short_folder(audio)}")

    @staticmethod
    def _audio_name(codec):
        return AUDIO_NAMES.get(codec_family(codec), codec.upper())

    def _check_fit(self, adjust=False):
        """Explain streams that can't be copied into the chosen format. With adjust, switch audio to a codec that fits;
        video is only ever flagged, since re-encoding it is slow and should be a deliberate choice."""
        notes = []
        self.note_action = None
        container = self.container.get()
        video = self.media.get(self.video_path.get())
        if video and not video.video:
            notes.append(f"{os.path.basename(self.video_path.get())} has no video in it. "
                         "Choose a video file, and put this one under Audio if it's the new sound.")
        if (video and video.video in VIDEO_FAMILIES and self.vcodec.get() == COPY
                and video.video not in CONTAINERS[container][1]):
            ext = os.path.splitext(self.video_path.get())[1].lstrip(".").lower()
            match = EXT_TO_CONTAINER.get(ext, "MKV")  # the video's own format, which holds it by definition
            if match == container:
                match = "MKV"
            notes.append(f"{VIDEO_NAMES[video.video]} video can't go into {container} as it is. "
                         f"Choose a video codec, or switch the format to {match}.")
            self.note_action = match
        note = ""
        source = self.audio_path.get() or self.video_path.get()
        if self.audio_info and self.audio_info[0] == source:
            codec = self.audio_info[1]
            if codec is None:
                if self.audio_path.get():
                    note = f"No audio track found in {os.path.basename(source)}."
            else:
                family = codec_family(codec)
                fits = container == "MKV" or family in CONTAINERS[container][1]
                if fits and adjust and self.auto_acodec and self.acodec.get() == self.auto_acodec:
                    # ReMux only converted because the old format needed it; this one doesn't, so keep the original.
                    self.auto_acodec = None
                    self.acodec.set(COPY)
                if not fits:
                    name = self._audio_name(codec)
                    if adjust and self.acodec.get() == COPY:
                        self._pick_fallback_audio(container, family)
                    choice = self.acodec.get()
                    if choice == COPY:
                        note = (f"{name} audio can't go into {container} as it is. "
                                "Choose an audio codec, or switch the format to MKV.")
                        self.note_action = self.note_action or "MKV"
                    elif choice == self.auto_acodec:
                        note = f"{name} audio can't go into {container} as it is, so it will be converted to {choice}."
                        if family in LOSSLESS and self.audio_encoders[choice].family not in LOSSLESS:
                            note += (" Most people can't hear the difference, and it plays everywhere. "
                                     "Use MOV to keep the audio lossless.")
                            self.note_action = self.note_action or "MOV"
        notes.append(note)
        note = "\n\n".join(filter(None, notes))
        self.audio_note.set(note)
        self._summarize()
        if note:
            self.note_label.grid()
        else:
            self.note_label.grid_remove()
        if self.note_action and self.note_action != container:
            self.note_btn.config(text=f"Use {self.note_action}")
            self.note_btn.grid()
        else:
            self.note_btn.grid_remove()

    def _pick_fallback_audio(self, container, family):
        prefer = (["alac"] if family in LOSSLESS and container == "MOV" else []) + FALLBACK_AUDIO[container]
        for wanted in prefer:
            for label, enc in self.audio_encoders.items():
                if enc.family == wanted:
                    self.auto_acodec = label
                    self.acodec.set(label)
                    return

    def _output_path(self):
        video = self.video_path.get()
        if not video:
            return None
        ext = CONTAINERS[self.container.get()][0]
        stem = os.path.splitext(os.path.basename(video))[0]
        return os.path.join(self.output_dir or os.path.dirname(video), f"{stem}_remux.{ext}")

    def _action(self):
        return "Replace Audio" if self.audio_path.get() else "Convert"

    def _doing(self):
        return "Replacing the audio" if self.audio_path.get() else "Converting"

    def _lock_inputs(self, locked):
        """While a job runs, the window must keep describing that job, so nothing that changes it stays live."""
        widgets = [self.video_btn, self.audio_btn, self.remove_btn, self.options_btn, self.folder_btn,
                   self.same_folder_btn, self.note_btn] + [box for _, box in self.option_rows]
        for widget in widgets:
            widget.state(["disabled"] if locked else ["!disabled"])

    def _set_busy(self, busy, status=""):
        # While busy the button cancels, so it drops the default (blue) look of the main action.
        self.run_btn.config(text="Cancel" if busy else self._action(), default="normal" if busy else "active",
                            style="TButton" if busy else "Primary.TButton")
        self.reveal_btn.grid_remove()
        self._lock_inputs(busy)
        self.progress.grid() if busy else self.progress.grid_remove()
        self.progress.stop()
        self.progress.config(mode="determinate", value=0)
        self.status_var.set(status)

    # ── encoder detection / ffmpeg install ────────────────────────────────────

    def _detect_encoders(self):
        self.status_var.set("Checking which codecs this computer can use…")

        def worker():
            video, audio = detect_encoders(self.ffmpeg)
            self.after(0, self._on_encoders_detected, video, audio)

        threading.Thread(target=worker, daemon=True).start()

    def _on_encoders_detected(self, video, audio):
        self.video_encoders = {enc.label: enc for enc in video}
        self.audio_encoders = {enc.label: enc for enc in audio}
        self._on_options_changed()
        self._probe_files()  # ffmpeg may have only just been installed
        if not self.running:
            self.status_var.set("")

    def _offer_ffmpeg_install(self):
        if self.installing:
            return
        tool, args, name = INSTALLERS.get(sys.platform, (None, None, None))
        installer = tool and find_tool(tool)
        if not installer:
            messagebox.showerror("ReMux needs ffmpeg", NEEDS_FFMPEG,
                                 detail=f"{MANUAL_INSTALL}\n\nOnce it's installed, try again. No need to restart ReMux.")
        elif messagebox.askyesno("ReMux needs ffmpeg", NEEDS_FFMPEG,
                                 detail=f"Install it now with {name}? It's free and takes a few minutes."):
            self.installing = True
            self.run_btn.config(state="disabled")
            self.progress.grid()
            self.progress.config(mode="indeterminate")
            self.progress.start(10)
            self.status_var.set(f"Installing ffmpeg with {name}… this can take a few minutes.")
            threading.Thread(target=self._install_ffmpeg, args=([installer, *args],), daemon=True).start()

    def _install_ffmpeg(self, cmd):
        result = run(cmd)
        self.after(0, self._on_install_done, result.returncode == 0, result.stderr or result.stdout)

    def _on_install_done(self, ok, log):
        self.installing = False
        self.run_btn.config(state="normal")
        self._set_busy(False)
        self.ffmpeg = find_tool("ffmpeg")
        if self.ffmpeg:
            self._detect_encoders()
        elif not ok:
            messagebox.showerror("Couldn't install ffmpeg", "The installer stopped with an error.",
                                 detail=f"{tail(log)}\n\nTry again, or install it yourself:\n{MANUAL_INSTALL}".strip())
        else:
            messagebox.showerror("Couldn't find ffmpeg", "The installer finished, but ReMux can't find ffmpeg.",
                                 detail="Restart ReMux. If that doesn't help, install it yourself:\n" + MANUAL_INSTALL)

    # ── remux ─────────────────────────────────────────────────────────────────

    def _on_return(self, _):
        focused = self.focus_get()
        if isinstance(focused, ttk.Button) and focused is not self.run_btn:
            if focused.instate(["!disabled"]):
                focused.invoke()  # e.g. Show in Finder after a save, or a picker reached with Tab
        elif not self.running and self.run_btn.instate(["!disabled"]):
            self._run_or_cancel()

    def _run_or_cancel(self):
        if self.running:
            self._cancel()
            return
        if not self.ffmpeg:
            self.ffmpeg = find_tool("ffmpeg")  # it may have been installed since ReMux started
            if self.ffmpeg:
                self._detect_encoders()
            else:
                self._offer_ffmpeg_install()
            return
        video, audio = self.video_path.get(), self.audio_path.get()
        if not video:
            messagebox.showwarning("Choose a video", "Choose the video you want to work on first.")
            return
        known = self.media.get(video)
        if known and not known.video:
            messagebox.showwarning("No video in this file", f"{os.path.basename(video)} has no video track.",
                                   detail="Choose a video file. If this is the new sound, choose it under Audio.")
            return
        if audio and self.audio_info == (audio, None):
            messagebox.showwarning("No audio in this file", f"{os.path.basename(audio)} has no audio track.",
                                   detail="Choose a different audio file.")
            return

        output = self._output_path()
        if os.path.exists(output):
            output = self._ask_existing(output)
            if not output:
                return
        if audio and os.path.exists(output) and os.path.samefile(audio, output):
            messagebox.showwarning("Audio and new file are the same",
                                   f"ReMux would save the new video over your audio file, {os.path.basename(output)}.",
                                   detail="Rename the audio file, or choose a different one.")
            return

        # Write to a temporary file beside the output, so an existing file is only replaced once the new one is done.
        stem, ext = os.path.splitext(os.path.basename(output))
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(output), prefix=f"{stem}.partial-", suffix=ext)
            os.close(fd)
        except OSError as e:
            messagebox.showerror("Can't save in this folder", f"ReMux can't save files in {os.path.dirname(output)}.",
                                 detail=f"{e.strerror}. Choose a different output folder, such as Documents.")
            return

        self.running, self.cancelled = True, False
        self.started_at = time.monotonic()
        self.job_output, self.tmp_output = output, tmp
        self._set_busy(True, f"{self._doing()}…")
        venc = self.video_encoders.get(self.vcodec.get())
        aenc = self.audio_encoders.get(self.acodec.get())
        # Lengths were already read when the files were picked; the worker only reads any that are missing.
        known = [getattr(self.media.get(path), "duration", None) for path in (video, audio)]
        threading.Thread(target=self._remux, args=(video, audio, venc, aenc, tmp, *known), daemon=True).start()

    def _ask_existing(self, output):
        """Ask what to do about a file that's already there, with buttons that say what they do.
        Returns the path to write, or None to stop."""
        keep = numbered(output)
        dialog = tk.Toplevel(self)
        dialog.title("")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        heading = font.nametofont("TkDefaultFont").copy()
        heading.configure(weight="bold")
        ttk.Label(frame, text=f"{os.path.basename(output)} already exists.", font=heading).pack(anchor="w")
        ttk.Label(frame, wraplength=380, style="Secondary.TLabel",
                  text=f"Keep both to save the new file as {os.path.basename(keep)}, or replace the old one. "
                       "If you replace it, the old file stays until the new one is finished.").pack(anchor="w",
                                                                                                    pady=(4, 16))
        choice = {"path": None}

        def pick(path):
            choice["path"] = path
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="Cancel", command=lambda: pick(None)).pack(side="left")
        ttk.Button(buttons, text="Replace", command=lambda: pick(output)).pack(side="left", padx=8)
        keep_btn = ttk.Button(buttons, text="Keep Both", default="active", command=lambda: pick(keep))
        keep_btn.pack(side="left")
        # Return presses the focused button, which is Keep Both until the user tabs away from it.
        dialog.bind("<Return>", lambda _: dialog.focus_get().invoke()
                    if isinstance(dialog.focus_get(), ttk.Button) else pick(keep))
        dialog.bind("<Escape>", lambda _: pick(None))
        dialog.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        dialog.geometry(f"+{x}+{self.winfo_rooty() + 80}")
        keep_btn.focus_set()
        dialog.grab_set()
        self.wait_window(dialog)
        return choice["path"]

    def _cancel(self):
        if not self.running:
            return
        if time.monotonic() - self.started_at > CONFIRM_CANCEL_AFTER and not messagebox.askyesno(
                f"Stop {self._doing().lower()}?", "The new file won't be saved.",
                detail="Your original files aren't touched either way.", icon="warning"):
            return
        if self.running:  # it may have finished while the question was open
            self.cancelled = True
            if self.proc:
                self.proc.terminate()

    def _build_command(self, video, audio, venc, aenc, output, shortest):
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats", "-progress", "pipe:1", "-y",
               "-i", video]
        if audio:
            cmd += ["-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a?"]
        cmd += ["-c:v", venc.name, *venc.args] if venc else ["-c:v", "copy"]
        cmd += ["-c:a", aenc.name, *aenc.args] if aenc else ["-c:a", "copy"]
        if shortest:
            cmd += ["-shortest"]
        if output.endswith((".mp4", ".mov")):
            cmd += ["-movflags", "+faststart"]
            if venc and venc.family == "hevc":
                cmd += ["-tag:v", "hvc1"]  # lets Apple players open HEVC
        return cmd + [output]

    def _remux(self, video, audio, venc, aenc, tmp, duration=None, audio_duration=None):
        duration = duration or media_info(self.ffmpeg, video).duration
        doing = self._doing()  # read here, before the worker loop, while it matches the job
        started = self.started_at
        if not duration:
            self.after(0, lambda: (self.progress.config(mode="indeterminate"), self.progress.start(10)))
        # Audio that runs past the end of the video is cut, rather than holding the last frame.
        # Shorter audio is left alone, so the video is never trimmed.
        audio_duration = audio and (audio_duration or media_info(self.ffmpeg, audio).duration)
        shortest = bool(duration and audio_duration and audio_duration > duration)
        cmd = self._build_command(video, audio, venc, aenc, tmp, shortest)

        with tempfile.TemporaryFile(mode="w+", errors="replace") as log:
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True, errors="replace",
                                             creationflags=NO_WINDOW)
            except OSError as e:
                self.after(0, self._on_finished, None, tmp, f"Couldn't start ffmpeg: {e.strerror}")
                return
            if self.cancelled:  # Cancel was pressed before ffmpeg started
                self.proc.terminate()
            for line in self.proc.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and duration and value.isdigit():
                    percent = min(100.0, int(value) / 1e6 / duration * 100)
                    elapsed = time.monotonic() - started
                    # The first few seconds include ffmpeg starting up, so wait before estimating.
                    estimate = 2 <= percent < 99 and elapsed >= 3
                    left = f" · {time_left(elapsed * (100 - percent) / percent)}" if estimate else ""
                    self.after(0, lambda p=percent, left=left: (self.progress.config(value=p),
                                                                self.status_var.set(f"{doing}… {p:.0f}%{left}")))
            code = self.proc.wait()
            log.seek(0)
            message = log.read().strip()
        self.after(0, self._on_finished, code, tmp, message)

    def _on_finished(self, code, tmp, message):
        self.proc, self.running, self.tmp_output = None, False, None
        self._set_busy(False)
        output = self.job_output
        if code == 0 and not self.cancelled:
            try:
                os.replace(tmp, output)  # only now is an existing file replaced
            except OSError as e:
                self._discard(tmp)
                self.status_var.set(f"Couldn't save {os.path.basename(output)}.")
                messagebox.showerror("Couldn't save the file", f"Couldn't save {os.path.basename(output)}.",
                                     detail=f"{e.strerror}. If it's open in another app, close it and try again.")
                return
            self.last_output = output
            self.just_saved = True
            self.status_var.set(f"Saved {os.path.basename(output)}")
            self.reveal_btn.grid()
            self.reveal_btn.focus_set()
            self._show_output()
            self._tell_if_away("Saved", os.path.basename(output))
            return

        self._discard(tmp)  # don't leave a half-written file behind
        if self.cancelled:
            self.status_var.set("Stopped. Nothing was saved.")
            return
        failed = "Couldn't replace the audio" if self.audio_path.get() else "Couldn't convert the video"
        self.status_var.set(f"{failed}.")
        self._tell_if_away(failed, os.path.basename(output))
        messagebox.showerror(failed, self._explain(message), detail=tail(message) or f"ffmpeg exited with code {code}.")

    def _explain(self, log):
        """Turn ffmpeg's error output into a sentence a non-expert can act on."""
        for pattern, text in FFMPEG_ERRORS:
            if re.search(pattern, log):
                return text.format(video=os.path.basename(self.video_path.get()),
                                   audio=os.path.basename(self.audio_path.get()))
        return "ffmpeg stopped with an error. The details below may help."

    def _tell_if_away(self, title, message):
        """When ReMux isn't the active app, say a job ended. In front, the status line and window already do."""
        if self.focus_get() is not None:
            return
        if not notify(title, message) and sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.FlashWindow(int(self.wm_frame(), 16), True)  # taskbar flash

    @staticmethod
    def _discard(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def _on_close(self):
        if self.installing and not messagebox.askyesno(
                "Quit ReMux?", "ReMux is still installing ffmpeg.",
                detail="Quit anyway? You can open ReMux again once the install has finished.", icon="warning"):
            return
        if self.running:
            if not messagebox.askyesno("Quit ReMux?", f"ReMux is still {self._doing().lower()}.",
                                       detail="Stop and quit? The new file won't be saved.", icon="warning"):
                return
            self.cancelled = True
            if self.proc:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            if self.tmp_output:
                self._discard(self.tmp_output)
        self.destroy()


if __name__ == "__main__":
    enable_dpi_awareness()
    ReMuxApp().mainloop()
