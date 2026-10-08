"""
Live Wallpaper Screensaver — self-contained, settings persisted, autostart.

- Settings window opens every time you launch the app.
- All settings (idle time, durations, cross-fade, shuffle) are saved to
  %APPDATA%\WallpaperScreensaver\settings.json and restored on launch.
- Media is stored in %APPDATA%\WallpaperScreensaver\media
- "Start with Windows" toggle registers/unregisters autostart in the
  HKCU registry Run key — no shortcut, no admin rights needed.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import cv2
import ctypes
import os
import glob
import random
import shutil
import sys
import subprocess
import time
import json
import numpy as np

try:
    import winreg
except ImportError:
    winreg = None

# ============================ PATHS ============================
def get_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~/.local/share")
    d = os.path.join(base, "WallpaperScreensaver")
    os.makedirs(d, exist_ok=True)
    return d

DATA_DIR = get_data_dir()
MEDIA_FOLDER = os.path.join(DATA_DIR, "media")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
os.makedirs(MEDIA_FOLDER, exist_ok=True)

# ==============================================================
#  DEFAULT SETTINGS (used if no settings file exists yet)
# ==============================================================
DEFAULT_SETTINGS = {
    "idle_minutes": 0,
    "idle_seconds": 3,
    "time_between_videos": 10,
    "image_duration_seconds": 5,
    "transition_seconds": 0.5,
    "shuffle": True,
}

# ============================ SETTINGS LOAD/SAVE ============================
def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for k in DEFAULT_SETTINGS:
                if k in data:
                    s[k] = data[k]
            print(f"Loaded settings from {SETTINGS_FILE}")
    except Exception as e:
        print(f"Failed to load settings: {e}")
    return s

def save_settings():
    try:
        data = {
            "idle_minutes": IDLE_MINUTES,
            "idle_seconds": IDLE_SECONDS,
            "time_between_videos": TIME_BETWEEN_VIDEOS,
            "image_duration_seconds": IMAGE_DURATION_SECONDS,
            "transition_seconds": TRANSITION_SECONDS,
            "shuffle": SHUFFLE,
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception as e:
        print(f"Failed to save settings: {e}")

# Apply loaded settings to module-level names
_loaded = load_settings()
IDLE_MINUTES = int(_loaded["idle_minutes"])
IDLE_SECONDS = int(_loaded["idle_seconds"])
TIME_BETWEEN_VIDEOS = int(_loaded["time_between_videos"])
IMAGE_DURATION_SECONDS = int(_loaded["image_duration_seconds"])
TRANSITION_SECONDS = float(_loaded["transition_seconds"])
SHUFFLE = bool(_loaded["shuffle"])

# ============================ OTHER CONFIG ============================
CHECK_INTERVAL_MS = 200
FIT_MODE = "cover"
TARGET_FPS = 60
STARTUP_GRACE_SECONDS = 5

APP_NAME = "WallpaperScreensaver"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ============================ File extension lists ============================
VIDEO_EXTS = ("*.mp4", "*.avi", "*.mov", "*.mkv", "*.wmv", "*.m4v", "*.flv")
IMAGE_EXTS = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.gif",
              "*.webp", "*.tiff", "*.tif")

# ============================ DPI awareness ============================
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ============================ Idle time (Windows) ============================
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def get_idle_seconds():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0

def get_cursor_pos():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

# ============================ Autostart (registry) ============================
def get_exe_path():
    """Return the command that should run at login."""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    else:
        # Running as a script -> use pythonw to hide the console
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.isfile(pyw):
            pyw = sys.executable
        script = os.path.abspath(__file__)
        return f'"{pyw}" "{script}"'

def is_autostart_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                            0, winreg.KEY_READ) as k:
            try:
                winreg.QueryValueEx(k, APP_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception:
        return False

def enable_autostart():
    if winreg is None:
        return False, "Autostart only supported on Windows."
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, get_exe_path())
        return True, ""
    except Exception as e:
        return False, str(e)

def disable_autostart():
    if winreg is None:
        return False, "Autostart only supported on Windows."
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                            0, winreg.KEY_ALL_ACCESS) as k:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass
        return True, ""
    except Exception as e:
        return False, str(e)

# ============================ Force window on top ============================
def force_topmost(widget):
    try:
        widget.update_idletasks()
        hwnd = widget.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        HWND_TOPMOST = -1
        SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        )
    except Exception:
        pass

# ============================ Folder helpers ============================
def open_folder_in_explorer(folder):
    if sys.platform == "win32":
        os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])

def scan_media(folder):
    items = []
    for e in VIDEO_EXTS:
        for f in glob.glob(os.path.join(folder, e)):
            items.append((f, "video"))
    for e in IMAGE_EXTS:
        for f in glob.glob(os.path.join(folder, e)):
            items.append((f, "image"))
    items.sort(key=lambda x: x[0].lower())
    return items

# ============================ Screensaver window ============================
root = tk.Tk()
root.withdraw()
root.configure(bg="black")

label = tk.Label(root, bg="black", bd=0, highlightthickness=0)
label.pack(fill="both", expand=True)

# ============================ Global state ============================
state = {"active": False}
running = True
MEDIA = []
current_index = 0
current_media = None
delay = 33
media_start_time = 0
START_TIME = time.time()

last_rendered = {"frame": None}
transition = {
    "active": False,
    "from": None,
    "to": None,
    "start": 0.0,
    "pending": None,
}

# ============================ Control window ============================
control = tk.Toplevel(root)
control.title("Live Wallpaper — Settings")
control.geometry("540x720+60+30")
control.resizable(False, False)
control.attributes("-topmost", True)

style = ttk.Style(control)
try:
    style.theme_use("vista")
except Exception:
    pass

# --- Header ---
header = ttk.Frame(control)
header.pack(fill="x", padx=20, pady=(15, 5))

ttk.Label(header, text="Live Wallpaper Screensaver",
          font=("Segoe UI", 14, "bold")).pack(anchor="w")

count_var = tk.StringVar(value="0 item(s)")
ttk.Label(header, textvariable=count_var,
          font=("Segoe UI", 9), foreground="#666").pack(anchor="w")

status_var = tk.StringVar(value="Waiting for idle...")
ttk.Label(header, textvariable=status_var,
          font=("Segoe UI", 9, "italic"),
          foreground="#666").pack(anchor="w", pady=(2, 0))

ttk.Separator(control, orient="horizontal").pack(fill="x", padx=20, pady=10)

# ============================ MEDIA SECTION ============================
media_frame = ttk.LabelFrame(control, text="  Media  ", padding=12)
media_frame.pack(fill="x", padx=20, pady=(0, 10))

def prompt_add_media():
    files = filedialog.askopenfilenames(
        parent=control,
        title="Select videos or images to add",
        filetypes=[
            ("All media",
             "*.mp4 *.avi *.mov *.mkv *.wmv *.m4v *.flv "
             "*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tiff *.tif"),
            ("Videos", "*.mp4 *.avi *.mov *.mkv *.wmv *.m4v *.flv"),
            ("Images", "*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tiff *.tif"),
            ("All files", "*.*"),
        ]
    )
    added = 0
    for f in files:
        try:
            dest = os.path.join(MEDIA_FOLDER, os.path.basename(f))
            shutil.copy2(f, dest)
            added += 1
        except Exception as e:
            print(f"Failed to copy {f}: {e}")
    return added

def on_add_media():
    added = prompt_add_media()
    if added:
        reload_playlist()
        messagebox.showinfo("Live Wallpaper",
                            f"Added {added} item(s).", parent=control)

def on_clear_media():
    if not MEDIA:
        messagebox.showinfo("Live Wallpaper",
                            "Nothing to clear.", parent=control)
        return
    if messagebox.askyesno("Live Wallpaper",
                           f"Remove all {len(MEDIA)} item(s)?",
                           parent=control):
        for f, _ in MEDIA:
            try:
                os.remove(f)
            except Exception:
                pass
        reload_playlist()

def on_reload():
    reload_playlist()
    messagebox.showinfo("Live Wallpaper",
                        f"Reloaded: {len(MEDIA)} item(s).",
                        parent=control)

ttk.Button(media_frame, text="Add Videos / Images...",
           command=on_add_media).pack(fill="x", pady=2)
ttk.Button(media_frame, text="Reload from storage",
           command=on_reload).pack(fill="x", pady=2)
ttk.Button(media_frame, text="Clear all media",
           command=on_clear_media).pack(fill="x", pady=2)

# ============================ SETTINGS SECTION ============================
settings_frame = ttk.LabelFrame(control, text="  Playback Settings  ",
                                padding=12)
settings_frame.pack(fill="x", padx=20, pady=(0, 10))

var_idle_min = tk.IntVar(value=IDLE_MINUTES)
var_idle_sec = tk.IntVar(value=IDLE_SECONDS)
var_video_dur = tk.IntVar(value=TIME_BETWEEN_VIDEOS)
var_image_dur = tk.IntVar(value=IMAGE_DURATION_SECONDS)
var_transition = tk.DoubleVar(value=TRANSITION_SECONDS)
var_shuffle = tk.BooleanVar(value=SHUFFLE)

# Auto-save flag to avoid writing on programmatic changes during startup
_suppress_save = {"on": False}

def apply_settings(*_):
    global IDLE_MINUTES, IDLE_SECONDS, TIME_BETWEEN_VIDEOS
    global IMAGE_DURATION_SECONDS, TRANSITION_SECONDS, SHUFFLE
    try:
        IDLE_MINUTES = max(0, int(var_idle_min.get()))
    except Exception:
        pass
    try:
        IDLE_SECONDS = max(0, min(59, int(var_idle_sec.get())))
    except Exception:
        pass
    try:
        TIME_BETWEEN_VIDEOS = max(0, int(var_video_dur.get()))
    except Exception:
        pass
    try:
        IMAGE_DURATION_SECONDS = max(1, int(var_image_dur.get()))
    except Exception:
        pass
    try:
        TRANSITION_SECONDS = max(0.0, min(5.0, float(var_transition.get())))
    except Exception:
        pass
    try:
        SHUFFLE = bool(var_shuffle.get())
    except Exception:
        pass
    # live-update the idle readout
    try:
        total = IDLE_MINUTES * 60 + IDLE_SECONDS
        idle_readout.set(f"({total} sec)")
    except Exception:
        pass
    # persist to disk
    if not _suppress_save["on"]:
        save_settings()

var_idle_min.trace_add("write", apply_settings)
var_idle_sec.trace_add("write", apply_settings)
var_video_dur.trace_add("write", apply_settings)
var_image_dur.trace_add("write", apply_settings)
var_transition.trace_add("write", apply_settings)
var_shuffle.trace_add("write", apply_settings)

settings_frame.columnconfigure(1, weight=1)

# Idle row (min + sec)
ttk.Label(settings_frame, text="Start after", font=("Segoe UI", 9)) \
    .grid(row=0, column=0, sticky="w", pady=5, padx=(0, 10))

idle_box = ttk.Frame(settings_frame)
idle_box.grid(row=0, column=1, sticky="w", pady=5)

ttk.Spinbox(idle_box, from_=0, to=120, increment=1,
            textvariable=var_idle_min, width=5,
            font=("Segoe UI", 9)).pack(side="left")
ttk.Label(idle_box, text="min", font=("Segoe UI", 9),
          foreground="#666").pack(side="left", padx=(4, 10))
ttk.Spinbox(idle_box, from_=0, to=59, increment=1,
            textvariable=var_idle_sec, width=5,
            font=("Segoe UI", 9)).pack(side="left")
ttk.Label(idle_box, text="sec", font=("Segoe UI", 9),
          foreground="#666").pack(side="left", padx=(4, 0))

idle_readout = tk.StringVar(
    value=f"({IDLE_MINUTES*60 + IDLE_SECONDS} sec)")
ttk.Label(settings_frame, textvariable=idle_readout,
          font=("Segoe UI", 8, "italic"), foreground="#888") \
    .grid(row=0, column=2, sticky="w", padx=(6, 0), pady=5)

def add_setting_row(parent, row, label_text, var, from_, to, inc, suffix):
    ttk.Label(parent, text=label_text, font=("Segoe UI", 9)) \
        .grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
    ttk.Spinbox(parent, from_=from_, to=to, increment=inc,
                textvariable=var, width=8, font=("Segoe UI", 9)) \
        .grid(row=row, column=1, sticky="w", pady=5)
    ttk.Label(parent, text=suffix, font=("Segoe UI", 9), foreground="#666") \
        .grid(row=row, column=2, sticky="w", padx=(6, 0), pady=5)

add_setting_row(settings_frame, 1, "Video duration",
                var_video_dur, 0, 3600, 1, "sec (0 = full)")
add_setting_row(settings_frame, 2, "Image duration",
                var_image_dur, 1, 3600, 1, "sec")
add_setting_row(settings_frame, 3, "Cross-fade",
                var_transition, 0.0, 3.0, 0.1, "sec (0 = off)")

ttk.Checkbutton(settings_frame, text="Shuffle playlist",
                variable=var_shuffle).grid(row=4, column=0, columnspan=3,
                                          sticky="w", pady=(8, 0))

ttk.Label(settings_frame,
          text="Settings are saved automatically.",
          font=("Segoe UI", 8, "italic"), foreground="#888") \
    .grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

# ============================ WINDOWS STARTUP SECTION ============================
startup_frame = ttk.LabelFrame(control, text="  Windows Startup  ", padding=12)
startup_frame.pack(fill="x", padx=20, pady=(0, 10))

var_autostart = tk.BooleanVar(value=is_autostart_enabled())
autostart_status = tk.StringVar(
    value="Will launch automatically when you log in."
    if var_autostart.get()
    else "Will not launch automatically."
)

def on_autostart_toggle():
    if var_autostart.get():
        ok, err = enable_autostart()
        if ok:
            autostart_status.set("Will launch automatically when you log in.")
        else:
            var_autostart.set(False)
            autostart_status.set(f"Failed: {err}")
            messagebox.showerror("Live Wallpaper",
                                 f"Could not enable autostart:\n{err}",
                                 parent=control)
    else:
        ok, err = disable_autostart()
        if ok:
            autostart_status.set("Will not launch automatically.")
        else:
            var_autostart.set(True)
            autostart_status.set(f"Failed: {err}")
            messagebox.showerror("Live Wallpaper",
                                 f"Could not disable autostart:\n{err}",
                                 parent=control)

ttk.Checkbutton(startup_frame,
                text="Start with Windows (recommended)",
                variable=var_autostart,
                command=on_autostart_toggle) \
    .pack(anchor="w")

ttk.Label(startup_frame, textvariable=autostart_status,
          font=("Segoe UI", 8, "italic"), foreground="#666") \
    .pack(anchor="w", padx=(20, 0), pady=(2, 0))

# ============================ ACTIONS ============================
actions_frame = ttk.Frame(control)
actions_frame.pack(fill="x", padx=20, pady=(0, 10))

def on_preview_now():
    if not MEDIA:
        messagebox.showinfo("Live Wallpaper",
                            "Add some media first.", parent=control)
        return
    stop_screensaver()
    start_screensaver()

def on_quit():
    on_close()

ttk.Button(actions_frame, text="▶  Preview Now",
           command=on_preview_now).pack(fill="x", pady=2)
ttk.Button(actions_frame, text="Hide window (runs in background)",
           command=control.withdraw).pack(fill="x", pady=2)
ttk.Button(actions_frame, text="Quit",
           command=on_quit).pack(fill="x", pady=2)

# ============================ FOOTER ============================
footer = ttk.Frame(control)
footer.pack(fill="x", padx=20, pady=(0, 5))

ttk.Button(footer, text="Open storage folder (advanced)",
           command=lambda: open_folder_in_explorer(MEDIA_FOLDER)) \
    .pack(fill="x")

ttk.Label(control,
          text="Media and settings are stored in your Windows user profile.\n"
               "No extra folders appear next to the program.",
          font=("Segoe UI", 7), foreground="#aaa",
          justify="center").pack(padx=20, pady=(0, 8))

# ============================ UI helpers ============================
def set_status(text):
    try:
        status_var.set(text)
    except Exception:
        pass

def refresh_count():
    try:
        n_v = sum(1 for _, k in MEDIA if k == "video")
        n_i = sum(1 for _, k in MEDIA if k == "image")
        count_var.set(f"{n_v} video(s)  •  {n_i} image(s)")
    except Exception:
        pass

# ============================ Playlist logic ============================
def reload_playlist():
    global MEDIA, current_index
    MEDIA = scan_media(MEDIA_FOLDER)
    if SHUFFLE:
        random.shuffle(MEDIA)
    if current_index >= len(MEDIA):
        current_index = 0
    print(f"Playlist: {len(MEDIA)} item(s)")
    refresh_count()

def _load_image(path):
    try:
        with open(path, "rb") as fh:
            data = np.frombuffer(fh.read(), np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception as e:
        print(f"Failed to load image {path}: {e}")
        return None

def open_media(index):
    path, kind = MEDIA[index]
    if kind == "video":
        c = cv2.VideoCapture(path)
        if not c.isOpened():
            c.release()
            return None
        f = c.get(cv2.CAP_PROP_FPS) or 30
        ret, first = c.read()
        if not ret:
            c.release()
            return None
        return {"kind": "video", "cap": c, "fps": f, "frame": first}
    else:
        img = _load_image(path)
        if img is None:
            return None
        return {"kind": "image", "cap": None, "fps": 0, "frame": img}

def _media_delay(media):
    if media["kind"] == "video":
        return max(1, int(1000 / min(media["fps"], TARGET_FPS)))
    return 33

# ============================ Fit / render ============================
def fit(frame, W, H):
    fh, fw = frame.shape[:2]
    if FIT_MODE == "stretch":
        return cv2.resize(frame, (W, H))
    scale = max(W / fw, H / fh)
    nw, nh = int(fw * scale), int(fh * scale)
    frame = cv2.resize(frame, (nw, nh))
    x = (nw - W) // 2
    y = (nh - H) // 2
    return frame[y:y+H, x:x+W]

def render(frame):
    W = root.winfo_screenwidth()
    H = root.winfo_screenheight()
    fitted = fit(frame, W, H)
    last_rendered["frame"] = fitted
    rgb = cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)
    img = ImageTk.PhotoImage(Image.fromarray(rgb))
    label.configure(image=img)
    label.image = img

def render_blend(from_f, to_f, alpha):
    alpha = max(0.0, min(1.0, alpha))
    blended = cv2.addWeighted(from_f, 1.0 - alpha, to_f, alpha, 0)
    rgb = cv2.cvtColor(blended, cv2.COLOR_BGR2RGB)
    img = ImageTk.PhotoImage(Image.fromarray(rgb))
    label.configure(image=img)
    label.image = img

# ============================ Media switching ============================
def request_next_media():
    global current_index
    if not MEDIA:
        return False

    for _ in range(len(MEDIA)):
        current_index = (current_index + 1) % len(MEDIA)
        result = open_media(current_index)
        if result is None:
            continue

        if TRANSITION_SECONDS > 0 and last_rendered["frame"] is not None:
            W = root.winfo_screenwidth()
            H = root.winfo_screenheight()
            to_frame = fit(result["frame"], W, H)
            transition["from"] = last_rendered["frame"]
            transition["to"] = to_frame
            transition["start"] = time.time()
            transition["pending"] = result
            transition["active"] = True
        else:
            switch_instantly(result)

        print(f"Next: {os.path.basename(MEDIA[current_index][0])}")
        return True

    return False

def switch_instantly(new_media):
    global current_media, delay, media_start_time
    old = current_media
    current_media = new_media
    if old is not None and old.get("cap") is not None:
        old["cap"].release()
    delay = _media_delay(new_media)
    media_start_time = time.time()
    render(new_media["frame"])

# ============================ Frame loop ============================
def next_frame():
    global media_start_time, current_media, delay

    if not running or not state["active"]:
        return

    if transition["active"]:
        elapsed = time.time() - transition["start"]
        alpha = elapsed / max(0.001, TRANSITION_SECONDS)
        render_blend(transition["from"], transition["to"], alpha)

        if alpha >= 1.0:
            transition["active"] = False
            new_media = transition["pending"]
            transition["pending"] = None

            old = current_media
            current_media = new_media
            if old is not None and old.get("cap") is not None:
                old["cap"].release()

            delay = _media_delay(new_media)
            media_start_time = time.time()

        root.after(20, next_frame)
        return

    if current_media is None:
        if not request_next_media():
            root.after(500, next_frame)
            return
        root.after(delay, next_frame)
        return

    kind = current_media["kind"]

    if kind == "image":
        if time.time() - media_start_time >= IMAGE_DURATION_SECONDS:
            if not request_next_media():
                root.after(200, next_frame)
                return
        root.after(100, next_frame)
        return

    if TIME_BETWEEN_VIDEOS > 0 and \
       time.time() - media_start_time >= TIME_BETWEEN_VIDEOS:
        if not request_next_media():
            root.after(delay, next_frame)
            return
        root.after(delay, next_frame)
        return

    ret, frame = current_media["cap"].read()
    if not ret:
        if not request_next_media():
            root.after(delay, next_frame)
            return
        root.after(delay, next_frame)
        return

    render(frame)
    root.after(delay, next_frame)

# ============================ Start / stop screensaver ============================
def start_screensaver():
    global current_media, delay, media_start_time, current_index
    if state["active"] or not MEDIA:
        return
    state["active"] = True
    set_status("Playing")

    W = root.winfo_screenwidth()
    H = root.winfo_screenheight()

    root.overrideredirect(True)
    root.geometry(f"{W}x{H}+0+0")
    root.attributes("-topmost", True)

    need_preload = (
        current_media is None
        or (current_media["kind"] == "video"
            and (current_media["cap"] is None
                 or not current_media["cap"].isOpened()))
    )
    if need_preload:
        result = open_media(current_index)
        if result is None:
            request_next_media()
        else:
            current_media = result
            delay = _media_delay(result)
            render(result["frame"])
            media_start_time = time.time()

    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.focus_force()
    force_topmost(root)

    next_frame()

def stop_screensaver():
    if not state["active"]:
        return
    state["active"] = False

    transition["active"] = False
    if transition["pending"] is not None:
        p = transition["pending"]
        if p.get("cap") is not None:
            p["cap"].release()
        transition["pending"] = None

    set_status("Waiting for idle...")
    root.withdraw()
    root.overrideredirect(False)
    root.attributes("-topmost", False)

# ============================ Control-window check ============================
def control_is_in_use():
    try:
        if not control.winfo_viewable():
            return False
    except Exception:
        return False
    try:
        fg = ctypes.windll.user32.GetForegroundWindow()
        hwnd = control.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        if fg == hwnd:
            return True
        x, y = get_cursor_pos()
        cx, cy = control.winfo_rootx(), control.winfo_rooty()
        cw, ch = control.winfo_width(), control.winfo_height()
        if cx <= x <= cx + cw and cy <= y <= cy + ch:
            return True
    except Exception:
        pass
    return False

# ============================ Idle watcher ============================
def watch_idle():
    if not running:
        return
    if time.time() - START_TIME < STARTUP_GRACE_SECONDS:
        root.after(CHECK_INTERVAL_MS, watch_idle)
        return
    idle = get_idle_seconds()
    if state["active"]:
        if idle < 0.5:
            stop_screensaver()
    else:
        total_idle = IDLE_MINUTES * 60 + IDLE_SECONDS
        if idle >= total_idle and MEDIA and not control_is_in_use():
            start_screensaver()
    root.after(CHECK_INTERVAL_MS, watch_idle)

# ============================ Quit ============================
def on_close(event=None):
    global running
    running = False
    save_settings()
    if current_media is not None and current_media.get("cap") is not None:
        current_media["cap"].release()
    if transition["pending"] is not None:
        p = transition["pending"]
        if p.get("cap") is not None:
            p["cap"].release()
    try:
        root.destroy()
    except tk.TclError:
        pass

root.bind("<Escape>", on_close)
control.protocol("WM_DELETE_WINDOW", on_close)

# ============================ Startup ============================
reload_playlist()
set_status("Waiting for idle...")
print(f"Data dir:    {DATA_DIR}")
print(f"Media dir:   {MEDIA_FOLDER}")
print(f"Settings:    {SETTINGS_FILE}")
print(f"Loaded:      {len(MEDIA)} item(s)")
print(f"Idle start:  {IDLE_MINUTES}min {IDLE_SECONDS}s")
print(f"Autostart:   {'ON' if is_autostart_enabled() else 'OFF'}")

# ============================ Go ============================
root.after(CHECK_INTERVAL_MS, watch_idle)
root.mainloop()