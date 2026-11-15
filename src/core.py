"""Core logic for StarStrings Manager: game detection, version checks, install/update/remove."""
from __future__ import annotations

import datetime as _dt
import functools
import hashlib
import json
import os
import re
import shutil
import string
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

APP_NAME = "StarStrings Manager"
APP_VERSION = "1.0.0"
APP_REPO = "CitizenRen/StarStrings-Manager"  # owner/name of this app's GitHub repo
APP_REPO_URL = f"https://github.com/{APP_REPO}"

AUTHOR_URL = "https://github.com/CitizenRen"
SS_AUTHOR_URL = "https://github.com/MrKraken"

SS_REPO = "MrKraken/StarStrings"
SS_REPO_URL = f"https://github.com/{SS_REPO}"
SS_RELEASE_TAG = "latest"  # LIVE build release
SS_ASSET_NAME = "StarStrings-LIVE.zip"
SS_README_URL = f"https://raw.githubusercontent.com/{SS_REPO}/master/readme.md"

USER_AGENT = f"StarStrings-Manager/{APP_VERSION}"
MARKER_NAME = ".starstrings_manager.json"
BACKUP_DIR_NAME = "StarStrings_Backups"

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "StarStrings Manager"
CONFIG_FILE = CONFIG_DIR / "settings.json"


# --------------------------------------------------------------------------- settings
def load_settings() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- http
def _get(url: str, accept: str = "application/vnd.github+json", timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str):
    return json.loads(_get(url).decode("utf-8"))


# --------------------------------------------------------------------------- game detection
def is_game_folder(path: Path) -> bool:
    return (path / "Data.p4k").is_file() or (path / "Bin64" / "StarCitizen.exe").is_file()


def normalise_game_folder(path: Path) -> Path:
    """Accept either the LIVE folder or the StarCitizen root and return the channel folder."""
    path = Path(path)
    if not is_game_folder(path) and is_game_folder(path / "LIVE"):
        return path / "LIVE"
    return path


@functools.lru_cache(maxsize=1)
def _launcher_text() -> str:
    """RSI Launcher store + logs (oldest first), read once and cached until refresh_cache()."""
    base = Path(os.environ.get("APPDATA", "")) / "rsilauncher"
    parts = []
    for f in (base / "launcher store.json", base / "logs" / "log.old.log", base / "logs" / "log.log"):
        try:
            parts.append(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return "\n".join(parts)


def refresh_cache() -> None:
    """Re-read launcher data (call from a worker thread: the scan is the slow part)."""
    _launcher_text.cache_clear()
    _launcher_versions.cache_clear()
    _launcher_versions()


_LAUNCHER_PATH = re.compile(r"([A-Za-z]:(?:\\\\|\\|/)[^\"\n,()]*?StarCitizen)(?:\\\\|\\|/)LIVE", re.I)
_LAUNCHER_VERSION = re.compile(r"(\d+\.\d+\.\d+)-[a-z]+\.(\d{5,})", re.I)


def _launcher_paths() -> list[Path]:
    """Install paths mentioned by the RSI Launcher."""
    return [Path(m.group(1).replace("\\\\", "\\")) / "LIVE" for m in _LAUNCHER_PATH.finditer(_launcher_text())]


def _common_paths() -> list[Path]:
    rels = [
        r"Program Files\Roberts Space Industries\StarCitizen",
        r"Roberts Space Industries\StarCitizen",
        r"Games\Roberts Space Industries\StarCitizen",
        r"Games\StarCitizen",
        r"StarCitizen",
        r"Star Citizen\StarCitizen",
    ]
    try:
        drives = os.listdrives()  # Windows, Python 3.12+: no probing of empty drive letters
    except (AttributeError, OSError):
        drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return [Path(d) / rel / "LIVE" for d in drives for rel in rels]


def detect_installs() -> list[Path]:
    seen, result = set(), []
    for p in _launcher_paths() + _common_paths():
        try:
            if not is_game_folder(p):
                continue
            key = str(p.resolve()).lower()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            result.append(p)
    _launcher_versions()  # warm the version cache here, off the UI thread
    return result


# --------------------------------------------------------------------------- game version
@dataclass
class GameVersion:
    branch: str = ""   # release branch from build_manifest.id, e.g. sc-alpha-4.10.0 (not bumped for hotfixes)
    change: str = ""   # P4 change number, unique per build
    patch: str = ""    # real patch version, e.g. 4.10.3

    @property
    def short(self) -> str:
        if not self.branch and not self.change and not self.patch:
            return "Unknown"
        v = self.patch or self.branch.replace("sc-alpha-", "")
        return f"{v}  ·  build {self.change}" if self.change else v


@functools.lru_cache(maxsize=1)  # scanning ~8 MB of logs costs ~140 ms; do it once per refresh
def _launcher_versions() -> dict[str, str]:
    """Map P4 change number -> patch version, from RSI Launcher logs ("4.10.3-live.13165946")."""
    return {m.group(2): m.group(1) for m in _LAUNCHER_VERSION.finditer(_launcher_text())}  # newest wins


def read_game_version(game: Path) -> GameVersion:
    """build_manifest.id gives the build's change number; its Branch field only names the
    release branch (4.10.0 for every 4.10.x hotfix), so the patch number is looked up from
    the launcher logs by change number."""
    try:
        data = json.loads((game / "build_manifest.id").read_text(encoding="utf-8")).get("Data", {})
    except Exception:
        return GameVersion()
    gv = GameVersion(data.get("Branch", ""), str(data.get("RequestedP4ChangeNum", "")))
    gv.patch = _launcher_versions().get(gv.change, "")
    return gv


# --------------------------------------------------------------------------- StarStrings paths
def _child_ci(parent: Path, name: str) -> Path:
    """Return an existing child matching `name` case-insensitively, else parent/name."""
    try:
        for c in parent.iterdir():
            if c.name.lower() == name.lower():
                return c
    except OSError:
        pass
    return parent / name


def localization_dir(game: Path) -> Path:
    data = _child_ci(game, "Data")
    loc = _child_ci(data, "Localization")
    return _child_ci(loc, "english")


def global_ini(game: Path) -> Path:
    return _child_ci(localization_dir(game), "global.ini")


def user_cfg(game: Path) -> Path:
    return _child_ci(game, "user.cfg")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- installed state
@dataclass
class InstalledState:
    present: bool = False
    release: str = ""          # release id we installed, "" if unknown
    published: str = ""        # asset timestamp of that release
    modified: bool = False     # global.ini changed since we installed it

    @property
    def label(self) -> str:
        if not self.present:
            return "Not installed"
        if not self.release:
            return "Installed (unknown version)"
        return pretty_release(self.release, self.published)


def read_installed(game: Path) -> InstalledState:
    ini = global_ini(game)
    if not ini.is_file():
        return InstalledState()
    state = InstalledState(present=True)
    try:
        marker = json.loads((ini.parent / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    state.release = marker.get("release", "")
    state.published = marker.get("published", "")
    st = ini.stat()
    if (st.st_size, st.st_mtime_ns) != (marker.get("size"), marker.get("mtime_ns")):
        # size/mtime differ from install time: only then pay for a full hash of the multi-MB file
        state.modified = marker.get("sha256") != _sha256(ini)
    return state


# --------------------------------------------------------------------------- latest release
@dataclass
class LatestRelease:
    release: str
    published: str
    download_url: str
    size: int
    built_for: GameVersion = field(default_factory=GameVersion)


def pretty_release(release: str, published: str = "") -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})-([0-9a-f]{5,})", release)
    if m:
        return f"{m.group(1)}  ·  {m.group(2)[:7]}"
    return release or (published[:10] if published else "Unknown")


def fetch_latest() -> LatestRelease:
    rel = _get_json(f"https://api.github.com/repos/{SS_REPO}/releases/tags/{SS_RELEASE_TAG}")
    asset = next((a for a in rel.get("assets", []) if a["name"].lower() == SS_ASSET_NAME.lower()), None)
    if asset is None:
        raise RuntimeError(f"{SS_ASSET_NAME} not found in the latest release")
    m = re.search(r"\((release-[^)]+)\)", rel.get("name", ""))
    release_id = m.group(1) if m else (asset.get("updated_at") or rel.get("published_at", ""))
    latest = LatestRelease(release_id, asset.get("updated_at", ""), asset["browser_download_url"], asset.get("size", 0))
    try:  # which game build the strings were generated from (from the repo readme)
        text = _get(SS_README_URL, accept="text/plain").decode("utf-8", "ignore")
        mm = re.search(r"Build:\s*(sc-alpha-[\d.]+)_\w+?_(\d+)", text)
        if mm:
            latest.built_for = GameVersion(mm.group(1), mm.group(2))
    except Exception:
        pass
    return latest


# --------------------------------------------------------------------------- app update
def _ver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def check_app_update() -> Optional[tuple[str, str]]:
    """Return (version, url) if a newer app release exists, else None."""
    try:
        rel = _get_json(f"https://api.github.com/repos/{APP_REPO}/releases/latest")
    except Exception:
        return None
    tag = rel.get("tag_name", "")
    if tag and _ver_tuple(tag) > _ver_tuple(APP_VERSION):
        return tag.lstrip("vV"), rel.get("html_url", APP_REPO_URL + "/releases")
    return None


# --------------------------------------------------------------------------- user.cfg merge
_CFG_LINE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*=\s*(.*?)\s*$")


def merge_user_cfg(target: Path, wanted: dict[str, str]) -> None:
    """Set each key in `wanted` inside target user.cfg, keeping everything else intact."""
    lines = target.read_text(encoding="utf-8", errors="ignore").splitlines() if target.is_file() else []
    by_lower = {k.lower(): k for k in wanted}
    done, out = set(), []
    for line in lines:
        m = _CFG_LINE.match(line)
        key = m.group(1).lower() if m else None
        if key in by_lower:
            if key not in done:  # update the first occurrence, drop duplicates
                out.append(f"{by_lower[key]} = {wanted[by_lower[key]]}")
                done.add(key)
        else:
            out.append(line)
    out += [f"{k} = {v}" for k, v in wanted.items() if k.lower() not in done]
    target.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


def parse_cfg(path: Path) -> dict[str, str]:
    """key = value pairs from a cfg file (comment lines never match _CFG_LINE)."""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return {m.group(1): m.group(2) for m in map(_CFG_LINE.match, lines) if m}


# --------------------------------------------------------------------------- backup / install / remove
def backup(game: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = game / BACKUP_DIR_NAME / stamp
    loc = localization_dir(game)
    rel = loc.relative_to(game)
    (dest / rel).mkdir(parents=True, exist_ok=True)
    for f in loc.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / rel / f.name)
    cfg = user_cfg(game)
    if cfg.is_file():
        shutil.copy2(cfg, dest / cfg.name)
    return dest


Progress = Callable[[float, str], None]


def install(game: Path, latest: LatestRelease, progress: Progress) -> None:
    with tempfile.TemporaryDirectory(prefix="ssm_") as tmp:
        tmp = Path(tmp)
        zpath = tmp / SS_ASSET_NAME
        progress(0.0, "Downloading StarStrings…")
        req = urllib.request.Request(latest.download_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or latest.size or 0)
            got, last_pct = 0, -1
            while chunk := r.read(1 << 16):
                f.write(chunk)
                got += len(chunk)
                pct = int(got * 100 / total) if total else -1
                if pct > last_pct:  # at most ~100 UI updates per download
                    last_pct = pct
                    progress(min(got / total, 1.0) * 0.85, f"Downloading… {got / 1e6:.1f} / {total / 1e6:.1f} MB")

        progress(0.88, "Extracting…")
        out = tmp / "x"
        with zipfile.ZipFile(zpath) as z:
            for m in z.namelist():  # guard against path traversal
                if Path(m).is_absolute() or ".." in Path(m).parts:
                    raise RuntimeError("Unsafe path in archive")
            z.extractall(out)

        src_ini = next((p for p in out.rglob("*") if p.name.lower() == "global.ini"), None)
        if src_ini is None:
            raise RuntimeError("global.ini not found in the download")
        src_cfg = next((p for p in out.rglob("*") if p.name.lower() == "user.cfg"), None)

        progress(0.93, "Installing…")
        loc = localization_dir(game)
        loc.mkdir(parents=True, exist_ok=True)
        # Copy every file that sits beside global.ini (normally just global.ini)
        for f in src_ini.parent.iterdir():
            if f.is_file():
                shutil.copy2(f, _child_ci(loc, f.name))

        wanted = {"g_language": "english"}
        if src_cfg is not None:
            wanted.update(parse_cfg(src_cfg))
        merge_user_cfg(user_cfg(game), wanted)

        dst_ini = global_ini(game)
        st = dst_ini.stat()
        marker = {
            "release": latest.release,
            "published": latest.published,
            "installed_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "sha256": _sha256(dst_ini),
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "app_version": APP_VERSION,
        }
        (loc / MARKER_NAME).write_text(json.dumps(marker, indent=2), encoding="utf-8")
        progress(1.0, "Done")


def remove(game: Path) -> None:
    loc = localization_dir(game)
    for name in ("global.ini", MARKER_NAME):
        p = _child_ci(loc, name)
        if p.is_file():
            p.unlink()
    # tidy empty folders (english -> Localization), never touch Data itself
    for d in (loc, loc.parent):
        try:
            d.rmdir()
        except OSError:
            break
    cfg = user_cfg(game)
    if cfg.is_file():
        keep = [l for l in cfg.read_text(encoding="utf-8", errors="ignore").splitlines()
                if not re.match(r"^\s*g_language\s*=", l, re.I)]
        if any(l.strip() for l in keep):
            cfg.write_text("\n".join(keep).rstrip("\n") + "\n", encoding="utf-8")
        else:
            cfg.unlink()
