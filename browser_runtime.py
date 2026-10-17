"""Resolve the OS HTTPS browser without changing the user's default or profile."""
import configparser
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys


@dataclass(frozen=True)
class DefaultBrowser:
    name: str
    executable: str = ""

    @property
    def controllable(self):
        # Unpatched Firefox and Safari cannot be driven by Playwright.
        name = Path(self.executable).name.lower()
        return bool(self.executable) and name in {
            "chrome", "chrome.exe", "google-chrome", "google-chrome-stable",
            "google-chrome-beta", "google-chrome-unstable", "chromium",
            "chromium-browser", "msedge", "msedge.exe", "microsoft-edge",
            "microsoft-edge-stable", "brave", "brave.exe", "brave-browser",
            "brave-browser-stable", "vivaldi", "vivaldi-bin", "vivaldi.exe",
            "opera", "opera.exe", "google chrome", "microsoft edge",
            "brave browser", "chromium.exe",
        }


def read_command(args):
    return subprocess.check_output(args, text=True, timeout=5, stderr=subprocess.DEVNULL).strip()


def windows_browser():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
        progid = winreg.QueryValueEx(key, "ProgId")[0]
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as key:
        command = os.path.expandvars(winreg.QueryValueEx(key, "")[0])
    # Windows paths may contain spaces; do not parse them using POSIX shlex.
    match = re.match(r'^\s*"([^"]+)"', command) or re.match(r'^\s*(.+?\.exe)(?:\s|$)', command, re.I)
    executable = match.group(1) if match else ""
    return DefaultBrowser(progid, executable if Path(executable).is_file() else "")


def linux_browser():
    try:
        desktop = read_command(["xdg-mime", "query", "default", "x-scheme-handler/https"])
    except (OSError, subprocess.SubprocessError):
        desktop = ""
    if not desktop:
        desktop = read_command(["xdg-settings", "get", "default-web-browser"])
    # Desktop IDs are filenames, never arbitrary paths.
    if not desktop or "/" in desktop or "\\" in desktop:
        return DefaultBrowser("系统默认浏览器")
    roots = [Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))]
    roots += [Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p]
    for root in roots:
        path = root / "applications" / desktop
        if not path.is_file():
            continue
        config = configparser.ConfigParser(interpolation=None)
        config.read(path, encoding="utf-8")
        entry = config["Desktop Entry"]
        # Only take the executable, never reuse personal profile or URL flags.
        args = shlex.split(entry.get("Exec", ""))
        executable = shutil.which(args[0]) if args else None
        return DefaultBrowser(entry.get("Name", desktop), executable or "")
    return DefaultBrowser(desktop)


def mac_browser():
    raw = subprocess.check_output(
        ["defaults", "export", "com.apple.LaunchServices/com.apple.launchservices.secure", "-"],
        timeout=5, stderr=subprocess.DEVNULL,
    )
    handlers = plistlib.loads(raw).get("LSHandlers", [])
    bundle = next((h.get("LSHandlerRoleAll") for h in handlers
                   if h.get("LSHandlerURLScheme") == "https"), None)
    # No explicit override means Safari is normally used; delegate to OS below.
    if not bundle:
        return DefaultBrowser("系统默认浏览器")
    script = "on run argv\nPOSIX path of (path to application id (item 1 of argv))\nend run"
    app = Path(read_command(["osascript", "-e", script, bundle]))
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
    return DefaultBrowser(info.get("CFBundleName", bundle), str(executable) if executable.is_file() else "")


def detect_default_browser():
    try:
        if sys.platform == "win32":
            return windows_browser()
        if sys.platform == "darwin":
            return mac_browser()
        return linux_browser()
    except (OSError, ValueError, KeyError, configparser.Error, subprocess.SubprocessError):
        # If resolution fails, the OS can still open the URL. Never pick another browser.
        return DefaultBrowser("系统默认浏览器")


def open_default(url):
    """Delegate only the public course URL to the OS, never credentials."""
    if sys.platform == "win32":
        os.startfile(url)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_default(playwright, browser):
    # No channel="chrome", bundled fallback, saved profile or remote-debug port.
    return playwright.chromium.launch(executable_path=browser.executable, headless=False)
