"""
JARVIS Video & Media Downloader Plugin.

Downloads videos, movies, audio, and media files from direct HTTP/HTTPS URLs across the
internet into the user's Downloads/JARVIS Media folder.

Features:
- Universal HTTP/HTTPS streaming media downloader.
- Background asynchronous downloading with progress tracking, speed calculation, and ETA.
- Chunked memory-safe streaming (no high RAM consumption).
- Safe filename sanitization and duplicate avoidance.
- Path sandboxing to prevent writing outside the user directory.
- Download management: inspect, start, list, cancel, and open download folder.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

_OS = platform.system()
_DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "JARVIS Media"
_CHUNK_SIZE = 64 * 1024  # 64 KB memory-safe stream chunk
_DEFAULT_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024 * 1024  # 50 GB
_LARGE_FILE_THRESHOLD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_DEFAULT_TIMEOUT_SECONDS = 60
_DEFAULT_MAX_RETRIES = 3
_MAX_CONCURRENT_DOWNLOADS = 3

# Dangerous / Executable extensions that must be sanitized
_DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".dll", ".scr", ".com",
    ".pif", ".hta", ".cpl", ".jar", ".sh", ".bin", ".py", ".vbe", ".jse", ".wsf",
    ".wsh", ".reg", ".apk", ".deb", ".rpm", ".iso", ".dmg",
}

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class DownloadItem:
    id: int
    url: str
    filename: str
    destination_dir: Path
    final_filepath: Path
    part_filepath: Path
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: Optional[int] = None
    status: str = "QUEUED"  # QUEUED | DOWNLOADING | COMPLETED | FAILED | CANCELLED
    start_time: float = field(default_factory=time.time)
    completion_time: Optional[float] = None
    error_message: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    mime_type: str = ""
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Helper & Security Functions
# ---------------------------------------------------------------------------

def _format_size(num_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    if num_bytes <= 0:
        return "Unknown size"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def _format_time(seconds: Optional[int]) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    if seconds is None or seconds < 0:
        return "calculating..."
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


_CRITICAL_SYSTEM_DIRS = {
    "c:\\windows", "c:\\windows\\system32", "c:\\program files", "c:\\program files (x86)",
    "/etc", "/bin", "/sbin", "/usr/bin", "/usr/sbin", "/boot", "/sys", "/proc"
}

def _resolve_and_validate_destination(custom_destination: Optional[str]) -> Tuple[bool, str, Path]:
    """
    Resolve user-specified destination folder or shortcut across any drive (C:, D:, E:, F:, external USBs, etc.).
    Supports drive letters, shortcuts, virtual explorer paths (e.g. 'This PC\\D:\\...'), auto-creates folders,
    and protects critical OS directories.
    """
    if not custom_destination or not str(custom_destination).strip():
        return True, "", _DEFAULT_DOWNLOAD_DIR

    raw = str(custom_destination).strip().strip('"').strip("'")

    # Strip Windows Explorer virtual folder prefixes if present (e.g. "This PC\D:\Movies" -> "D:\Movies")
    if raw.lower().startswith("this pc\\") or raw.lower().startswith("this pc/"):
        raw = raw[8:].strip()
    elif raw.lower().startswith("bu bilgisayar\\") or raw.lower().startswith("bu bilgisayar/"):
        raw = raw[14:].strip()

    # Handle bare Windows drive roots like "D:" -> "D:\"
    if re.match(r"^[a-zA-Z]:$", raw):
        raw += "\\"

    lower = raw.lower()

    shortcuts: Dict[str, Path] = {
        "downloads": Path.home() / "Downloads" / "JARVIS Media",
        "download": Path.home() / "Downloads" / "JARVIS Media",
        "desktop": Path.home() / "Desktop",
        "documents": Path.home() / "Documents",
        "videos": Path.home() / "Videos",
        "movies": Path.home() / "Videos" / "Movies",
        "music": Path.home() / "Music",
        "pictures": Path.home() / "Pictures",
        "home": Path.home(),
    }

    if lower in shortcuts:
        target = shortcuts[lower]
    else:
        head, sep, rest = raw.replace("\\", "/").partition("/")
        if sep and head.lower() in shortcuts:
            target = shortcuts[head.lower()] / rest.strip("/")
        else:
            try:
                target = Path(raw).expanduser().resolve()
            except Exception as e:
                return False, f"Invalid destination path '{raw}': {e}", _DEFAULT_DOWNLOAD_DIR

    # Prevent saving directly to critical OS system directories
    target_str = str(target).lower()
    for sys_dir in _CRITICAL_SYSTEM_DIRS:
        if target_str == sys_dir or target_str.startswith(sys_dir + "\\") or target_str.startswith(sys_dir + "/"):
            return False, f"Security restriction: Cannot save files directly to critical system directory '{target}'.", _DEFAULT_DOWNLOAD_DIR

    # Verify drive accessibility on Windows
    if _OS == "Windows":
        drive = target.drive
        if drive and not Path(drive + "\\").exists():
            return False, f"Drive '{drive}' is not connected or accessible.", _DEFAULT_DOWNLOAD_DIR

    return True, "", target


def _is_safe_sandbox_path(target: Path) -> bool:
    """Check that destination path is safe across any drive and not a critical OS system path."""
    valid, _, _ = _resolve_and_validate_destination(str(target))
    return valid


def _sanitize_filename(raw_name: str, default_ext: str = ".mp4") -> str:
    """
    Sanitize filename to prevent path traversal and invalid filesystem characters.
    Strictly removes '../', directory separators, control chars, and Windows forbidden chars.
    """
    if not raw_name or not isinstance(raw_name, str):
        raw_name = f"video_{int(time.time())}{default_ext}"

    # Remove URL decoding artifacts / slashes
    clean = urllib.parse.unquote(raw_name)
    clean = clean.replace("\\", "/").split("/")[-1]

    # Strip dangerous path traversal sequences
    clean = re.sub(r"\.\.+", "", clean)

    # Strip invalid Windows filename characters: < > : " / \ | ? * and ASCII control chars (0-31)
    clean = re.sub(r'[\<\>\:"/\\\|\?\*\x00-\x1f]', "_", clean)
    clean = clean.strip(". ")

    if not clean:
        clean = f"video_{int(time.time())}{default_ext}"

    # Limit filename length
    stem, ext = os.path.splitext(clean)
    if len(stem) > 100:
        stem = stem[:100]
    if not ext:
        ext = default_ext
    clean = f"{stem}{ext}"

    # Check for dangerous executable extensions
    ext_lower = os.path.splitext(clean)[1].lower()
    if ext_lower in _DANGEROUS_EXTENSIONS:
        clean = f"{stem}_video{default_ext}"

    return clean


def _check_ssrf(hostname: str, allow_local: bool = False) -> Tuple[bool, str]:
    """Check hostname against localhost/loopback SSRF vulnerabilities."""
    if allow_local:
        return True, ""

    hostname_lower = hostname.lower().strip()
    if hostname_lower in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return False, "Access to localhost/loopback addresses is restricted."

    try:
        addr_info = socket.getaddrinfo(hostname_lower, None)
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)

            if ip_obj.is_loopback:
                return False, f"Loopback address ({ip_str}) is restricted."
            if ip_obj.is_unspecified:
                return False, f"Unspecified address ({ip_str}) is restricted."
    except Exception:
        pass

    return True, ""


# ---------------------------------------------------------------------------
# Download Manager
# ---------------------------------------------------------------------------

class MediaDownloaderManager:
    """Thread-safe download manager for streaming media downloads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._downloads: Dict[int, DownloadItem] = {}
        self._next_id: int = 1
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_CONCURRENT_DOWNLOADS,
            thread_name_prefix="JarvisDownloader"
        )

    def validate_url(self, url: str, allow_local: bool = False) -> Tuple[bool, str, Optional[urllib.parse.ParseResult]]:
        """Validate URL syntax and scheme."""
        if not url or not isinstance(url, str):
            return False, "Please provide a valid video or media URL.", None

        url = url.strip()
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception as e:
            return False, f"Invalid URL format: {e}", None

        if parsed.scheme not in {"http", "https"}:
            return False, f"Unsupported protocol '{parsed.scheme}://'. Only HTTP and HTTPS URLs are supported.", None

        if not parsed.hostname:
            return False, "URL is missing a valid host name.", None

        safe_host, host_err = _check_ssrf(parsed.hostname, allow_local=allow_local)
        if not safe_host:
            return False, f"Security restriction: {host_err}", None

        return True, "", parsed

    def inspect_media(self, url: str, allow_local: bool = False) -> Dict[str, Any]:
        """Inspect remote media metadata via HEAD/Ranged GET request without downloading."""
        is_valid, err_msg, parsed = self.validate_url(url, allow_local=allow_local)
        if not is_valid:
            return {
                "valid": False,
                "error": err_msg,
                "url": url,
            }

        headers = dict(_DEFAULT_HEADERS)
        if parsed and parsed.netloc:
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        status_code = 200
        content_type = "video/mp4"
        content_length = 0
        disposition = ""
        final_url = url

        # Attempt HEAD request first
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT_SECONDS) as resp:
                status_code = resp.getcode()
                content_type = resp.headers.get("Content-Type", "").lower()
                content_length = int(resp.headers.get("Content-Length", 0))
                disposition = resp.headers.get("Content-Disposition", "")
                final_url = resp.geturl()
        except Exception:
            # Fallback to a ranged GET for servers that block HEAD requests
            try:
                get_req = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-1024"}, method="GET")
                with urllib.request.urlopen(get_req, timeout=_DEFAULT_TIMEOUT_SECONDS) as resp:
                    status_code = resp.getcode()
                    content_type = resp.headers.get("Content-Type", "").lower()
                    crange = resp.headers.get("Content-Range", "")
                    content_length = 0
                    if "/" in crange:
                        try:
                            content_length = int(crange.split("/")[-1])
                        except ValueError:
                            content_length = int(resp.headers.get("Content-Length", 0))
                    else:
                        content_length = int(resp.headers.get("Content-Length", 0))
                    disposition = resp.headers.get("Content-Disposition", "")
                    final_url = resp.geturl()
            except Exception:
                # If server rejects metadata probing, proceed with defaults so download is not blocked
                pass

        # Filename extraction
        raw_name = ""
        if disposition and "filename=" in disposition:
            match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)["\']?', disposition, re.IGNORECASE)
            if match:
                raw_name = match.group(1)

        if not raw_name:
            path_part = urllib.parse.urlparse(final_url).path
            raw_name = os.path.basename(path_part)

        ext = os.path.splitext(raw_name)[1].lower() if raw_name else ""
        if not ext:
            if "video/mp4" in content_type:
                ext = ".mp4"
            elif "video/webm" in content_type:
                ext = ".webm"
            elif "video/x-matroska" in content_type:
                ext = ".mkv"
            elif "audio/mpeg" in content_type or "audio/mp3" in content_type:
                ext = ".mp3"
            elif "audio/wav" in content_type:
                ext = ".wav"
            elif "audio/ogg" in content_type or "video/ogg" in content_type:
                ext = ".ogg"
            else:
                ext = ".mp4"

        sanitized_name = _sanitize_filename(raw_name or f"video_{int(time.time())}{ext}", default_ext=ext)
        needs_confirmation = content_length >= _LARGE_FILE_THRESHOLD_BYTES

        return {
            "valid": True,
            "url": url,
            "final_url": final_url,
            "filename": sanitized_name,
            "size_bytes": content_length,
            "formatted_size": _format_size(content_length),
            "mime_type": content_type or "video/mp4",
            "needs_confirmation": needs_confirmation,
            "destination": _DEFAULT_DOWNLOAD_DIR,
        }

    def start_download(
        self,
        url: str,
        destination: Optional[str] = None,
        confirm_large: bool = False,
        player: Any = None,
        allow_local: bool = False,
    ) -> Tuple[bool, str, Optional[int]]:
        """Start streaming download in a background worker."""
        info = self.inspect_media(url, allow_local=allow_local)
        if not info.get("valid"):
            return False, info.get("error", "Invalid media URL."), None

        size_bytes = info.get("size_bytes", 0)
        formatted_size = info.get("formatted_size", "Unknown size")
        filename = info.get("filename", "video.mp4")

        # Large file confirmation check
        if size_bytes >= _LARGE_FILE_THRESHOLD_BYTES and not confirm_large:
            return True, (
                f"I found a large media file: '{filename}' ({formatted_size}). "
                f"Destination: Downloads/JARVIS Media. Please confirm if you want to proceed with the download."
            ), None

        # Resolve and validate destination directory across any drive/folder
        dest_valid, dest_err, dest_dir = _resolve_and_validate_destination(destination)
        if not dest_valid:
            return False, dest_err, None

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return False, f"Failed to create download folder '{dest_dir}': {e}", None

        # Check free disk space
        try:
            usage = shutil.disk_usage(dest_dir)
            required_space = size_bytes + (200 * 1024 * 1024) if size_bytes > 0 else (500 * 1024 * 1024)
            if usage.free < required_space:
                return False, f"Insufficient disk space. Free: {_format_size(usage.free)}, Required: {_format_size(required_space)}.", None
        except Exception:
            pass

        # Handle duplicate filenames safely
        base_stem, ext = os.path.splitext(filename)
        final_path = dest_dir / filename
        counter = 1
        while final_path.exists():
            final_path = dest_dir / f"{base_stem} ({counter}){ext}"
            counter += 1

        part_path = dest_dir / f"{final_path.name}.jarvis_part"

        with self._lock:
            download_id = self._next_id
            self._next_id += 1

            item = DownloadItem(
                id=download_id,
                url=url,
                filename=final_path.name,
                destination_dir=dest_dir,
                final_filepath=final_path,
                part_filepath=part_path,
                total_bytes=size_bytes,
                status="DOWNLOADING",
                mime_type=info.get("mime_type", "video/mp4"),
            )
            self._downloads[download_id] = item

        # Dispatch download worker in background thread pool
        self._executor.submit(self._download_worker, item, player, allow_local)

        msg = (
            f"Download #{download_id} started for '{item.filename}' ({formatted_size}). "
            f"Saving to {dest_dir}."
        )
        print(f"[JARVIS-DOWNLOADER] [START] {msg}")
        if player:
            try:
                player.write_log(f"JARVIS: {msg}")
            except Exception:
                pass

        return True, msg, download_id

    def _download_worker(self, item: DownloadItem, player: Any = None, allow_local: bool = False) -> None:
        """Background worker performing chunked streaming download with progress tracking and retries."""
        last_log_time = 0.0
        bytes_at_last_log = 0
        speed_window_start = time.time()
        speed_window_bytes = 0

        parsed = urllib.parse.urlparse(item.url)
        headers = dict(_DEFAULT_HEADERS)
        if parsed and parsed.netloc:
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        for attempt in range(1, _DEFAULT_MAX_RETRIES + 1):
            if item.cancel_event.is_set():
                self._cleanup_partial(item, "CANCELLED")
                return

            item.retry_count = attempt
            try:
                req = urllib.request.Request(item.url, headers=headers)

                # Open streaming connection
                with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT_SECONDS) as resp:
                    status_code = resp.getcode()
                    if status_code not in (200, 206):
                        raise urllib.error.HTTPError(item.url, status_code, f"Unexpected HTTP status {status_code}", resp.headers, None)

                    content_length = int(resp.headers.get("Content-Length", 0))
                    if content_length > 0 and item.total_bytes == 0:
                        item.total_bytes = content_length

                    cancelled = False
                    with open(item.part_filepath, "wb") as f_out:
                        while True:
                            if item.cancel_event.is_set():
                                cancelled = True
                                break

                            chunk = resp.read(_CHUNK_SIZE)
                            if not chunk:
                                break

                            f_out.write(chunk)
                            item.downloaded_bytes += len(chunk)
                            speed_window_bytes += len(chunk)

                            now = time.time()
                            window_elapsed = now - speed_window_start
                            if window_elapsed >= 1.0:
                                item.speed_bps = speed_window_bytes / window_elapsed
                                speed_window_bytes = 0
                                speed_window_start = now

                                if item.total_bytes > 0 and item.speed_bps > 0:
                                    remaining = item.total_bytes - item.downloaded_bytes
                                    item.eta_seconds = int(remaining / item.speed_bps)

                            # Throttled progress logging (at most once every 2 seconds)
                            if now - last_log_time >= 2.0:
                                last_log_time = now
                                pct = (item.downloaded_bytes / item.total_bytes * 100.0) if item.total_bytes > 0 else 0.0
                                speed_str = _format_size(int(item.speed_bps)) + "/s"
                                eta_str = _format_time(item.eta_seconds)
                                dl_str = _format_size(item.downloaded_bytes)
                                tot_str = _format_size(item.total_bytes) if item.total_bytes > 0 else "Unknown"

                                log_line = f"[{pct:5.1f}%] {item.filename} | {dl_str}/{tot_str} | Speed: {speed_str} | ETA: {eta_str}"
                                print(f"[JARVIS-DOWNLOADER] [PROGRESS] {log_line}")

                    if cancelled or item.cancel_event.is_set():
                        self._cleanup_partial(item, "CANCELLED")
                        print(f"[JARVIS-DOWNLOADER] [CANCEL] Download #{item.id} cancelled by user.")
                        return

                    # Download finished successfully - atomically rename part file
                    if item.part_filepath.exists():
                        if item.final_filepath.exists():
                            item.final_filepath.unlink()
                        item.part_filepath.rename(item.final_filepath)

                    item.status = "COMPLETED"
                    item.completion_time = time.time()
                    success_msg = f"Download #{item.id} completed: '{item.filename}' ({_format_size(item.downloaded_bytes)}) saved to {item.destination_dir}."
                    print(f"[JARVIS-DOWNLOADER] [DONE] {success_msg}")
                    if player:
                        try:
                            player.write_log(f"JARVIS: {success_msg}")
                        except Exception:
                            pass
                    return

            except Exception as e:
                item.error_message = str(e)
                print(f"[JARVIS-DOWNLOADER] [RETRY] Attempt {attempt}/{_DEFAULT_MAX_RETRIES} failed for #{item.id}: {e}")
                if attempt < _DEFAULT_MAX_RETRIES and not item.cancel_event.is_set():
                    time.sleep(2.0 * attempt)
                else:
                    self._cleanup_partial(item, "FAILED")
                    fail_msg = f"Download #{item.id} failed: {e}"
                    print(f"[JARVIS-DOWNLOADER] [ERROR] {fail_msg}")
                    if player:
                        try:
                            player.write_log(f"JARVIS: {fail_msg}")
                        except Exception:
                            pass
                    return

    def _cleanup_partial(self, item: DownloadItem, new_status: str) -> None:
        """Clean up .jarvis_part file and update item status."""
        item.status = new_status
        try:
            if item.part_filepath and item.part_filepath.exists():
                item.part_filepath.unlink()
        except Exception:
            pass

    def cancel_download(self, download_id: Optional[int] = None) -> Tuple[bool, str]:
        """Cancel an active download by ID or cancel all active downloads if ID is None."""
        with self._lock:
            if download_id is not None:
                item = self._downloads.get(download_id)
                if not item:
                    return False, f"Download #{download_id} not found."
                if item.status not in ("QUEUED", "DOWNLOADING"):
                    return False, f"Download #{download_id} is already in status '{item.status}'."
                item.cancel_event.set()
                return True, f"Cancelling download #{download_id} ('{item.filename}')."
            else:
                active_count = 0
                for it in self._downloads.values():
                    if it.status in ("QUEUED", "DOWNLOADING"):
                        it.cancel_event.set()
                        active_count += 1
                if active_count == 0:
                    return True, "No active downloads to cancel."
                return True, f"Cancelled {active_count} active download(s)."

    def list_downloads(self) -> str:
        """Summarize all tracked downloads with status and progress."""
        with self._lock:
            if not self._downloads:
                return "No downloads recorded in this session."

            lines = ["JARVIS Media Downloads:"]
            for it in sorted(self._downloads.values(), key=lambda x: x.id, reverse=True):
                if it.status == "DOWNLOADING":
                    pct = (it.downloaded_bytes / it.total_bytes * 100.0) if it.total_bytes > 0 else 0.0
                    spd = _format_size(int(it.speed_bps)) + "/s"
                    eta = _format_time(it.eta_seconds)
                    lines.append(
                        f"• #{it.id} [DOWNLOADING {pct:.1f}%] {it.filename} - {_format_size(it.downloaded_bytes)}/{_format_size(it.total_bytes)} "
                        f"({spd}, ETA: {eta})"
                    )
                elif it.status == "COMPLETED":
                    lines.append(f"• #{it.id} [COMPLETED] {it.filename} ({_format_size(it.downloaded_bytes)})")
                elif it.status == "CANCELLED":
                    lines.append(f"• #{it.id} [CANCELLED] {it.filename}")
                elif it.status == "FAILED":
                    lines.append(f"• #{it.id} [FAILED] {it.filename} (Error: {it.error_message})")
                else:
                    lines.append(f"• #{it.id} [{it.status}] {it.filename}")

            return "\n".join(lines)

    def get_download(self, download_id: int) -> Optional[DownloadItem]:
        with self._lock:
            return self._downloads.get(download_id)

    def open_download_folder(self, custom_path: Optional[str] = None) -> Tuple[bool, str]:
        """Safely open download folder in OS file manager."""
        dest_valid, dest_err, target = _resolve_and_validate_destination(custom_path)
        if not dest_valid:
            return False, dest_err

        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            if _OS == "Windows":
                os.startfile(str(target))
            elif _OS == "Darwin":
                import subprocess
                subprocess.Popen(["open", str(target)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
            return True, f"Opened download folder: {target}"
        except Exception as e:
            return False, f"Failed to open download folder '{target}': {e}"


# Global instance
manager = MediaDownloaderManager()

# ---------------------------------------------------------------------------
# Plugin Declaration for JARVIS
# ---------------------------------------------------------------------------

PLUGIN = {
    "name": "media_downloader",
    "description": (
        "Download any video, movie, or media files from any HTTP/HTTPS URL across the internet "
        "to any drive or folder (e.g. C:, D:, E:, F:, Downloads, Desktop, Videos, Movies, custom paths) with user approval. "
        "Actions: 'download' (start downloading a media URL to default or user-approved drive/path), "
        "'inspect' (check media size and format before downloading), 'list' (view active/completed downloads), "
        "'cancel' (cancel an ongoing download by ID), and 'open_folder' (open the downloads folder on any drive)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action to perform: 'download', 'inspect', 'list', 'cancel', or 'open_folder'."
            },
            "url": {
                "type": "STRING",
                "description": "HTTP/HTTPS media URL to download or inspect."
            },
            "download_id": {
                "type": "INTEGER",
                "description": "Optional download ID for cancellation or status query."
            },
            "destination": {
                "type": "STRING",
                "description": "Optional custom drive or destination folder approved by the user (e.g. 'D:/Movies', 'E:/Downloads', 'D:', 'Desktop', 'Videos', 'F:/')."
            },
            "confirm_large": {
                "type": "BOOLEAN",
                "description": "Set to True if the user explicitly confirmed downloading a large file."
            }
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point for JARVIS plugin execution."""
    action = str(parameters.get("action", "")).strip().lower()
    url = str(parameters.get("url", "")).strip()
    download_id = parameters.get("download_id")
    destination = parameters.get("destination")
    confirm_large = bool(parameters.get("confirm_large", False))

    if not action or action == "download":
        if not url:
            return "Please provide a media URL to download."
        success, msg, _ = manager.start_download(
            url=url,
            destination=destination,
            confirm_large=confirm_large,
            player=player,
        )
        return msg

    elif action == "inspect":
        if not url:
            return "Please provide a media URL to inspect."
        info = manager.inspect_media(url)
        if not info.get("valid"):
            return f"Cannot download media: {info.get('error', 'Unknown error')}"
        dest_display = destination if destination else "Downloads/JARVIS Media (or any user-approved drive/path)"
        return (
            f"Media found.\n"
            f"File: {info['filename']}\n"
            f"Size: {info['formatted_size']}\n"
            f"Type: {info.get('mime_type', 'media')}\n"
            f"Destination: {dest_display}\n"
            f"Ready to download. You can save to the default folder or any drive/folder of your choice."
        )

    elif action in ("list", "status", "show"):
        return manager.list_downloads()

    elif action == "cancel":
        int_id = None
        if download_id is not None:
            try:
                int_id = int(download_id)
            except (ValueError, TypeError):
                int_id = None
        _, msg = manager.cancel_download(int_id)
        return msg

    elif action in ("open_folder", "open_download_folder", "open_downloads"):
        _, msg = manager.open_download_folder(destination)
        return msg

    else:
        return f"Unknown media downloader action '{action}'. Valid actions are: download, inspect, list, cancel, open_folder."
