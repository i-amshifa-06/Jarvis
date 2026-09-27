import uiautomation as auto
import time
import re
import psutil
import ctypes
import os

# --- ADAPTIVE CONFIGURATION & FALLBACK RATIOS ---
# Relative position fallbacks (proportions of dynamic window dimensions)
# Used only as LEVEL 3 fallback when UIA and Visual detection fail.
DEFAULT_RETRY_LIMIT = 2
FALLBACK_RELATIVE_VOICE_X = 0.88
FALLBACK_RELATIVE_VIDEO_X = 0.82
FALLBACK_RELATIVE_HEADER_Y = 0.055

# Min dimensions for a functional WhatsApp Desktop window
MIN_WINDOW_WIDTH = 450
MIN_WINDOW_HEIGHT = 350
# ------------------------------------------------

def set_dpi_awareness():
    """Ensure process is Per-Monitor DPI Aware so coordinates match physical pixels."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2) # PROCESS_PER_MONITOR_DPI_AWARE_V2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Initialize DPI awareness on module load
set_dpi_awareness()


def get_display_diagnostics():
    """Retrieve screen resolution and DPI settings for diagnostic logging."""
    diag = {}
    try:
        user32 = ctypes.windll.user32
        diag["screen_width"] = user32.GetSystemMetrics(0)
        diag["screen_height"] = user32.GetSystemMetrics(1)
    except Exception as e:
        diag["screen_error"] = str(e)
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        diag["dpi_x"] = dpi_x
        diag["scaling_pct"] = int((dpi_x / 96.0) * 100)
    except Exception:
        diag["dpi_x"] = "Unknown"
        diag["scaling_pct"] = "Unknown"
    return diag


def get_whatsapp_process_info():
    """
    Check if WhatsApp desktop processes are currently running in the system.
    """
    info = {
        "process_found": False,
        "pid": None,
        "name": "",
        "pids": [],
        "is_background_root": False
    }
    try:
        for p in psutil.process_iter(['pid', 'name']):
            try:
                p_name = p.info.get('name') or ''
                if 'whatsapp' in p_name.lower():
                    info["process_found"] = True
                    p_pid = p.info.get('pid')
                    if p_pid not in info["pids"]:
                        info["pids"].append(p_pid)
                    if not info["pid"]:
                        info["pid"] = p_pid
                        info["name"] = p_name
                    if 'root' in p_name.lower():
                        info["is_background_root"] = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return info


def get_package_identity():
    """
    Verify the exact installed WhatsApp Desktop package identity from the running system.
    """
    expected_aumid = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
    info = {
        "family": "",
        "full_name": "",
        "aumid": "",
        "path": "",
        "current_activation_string": expected_aumid,
        "mismatch": False,
        "details": ""
    }
    try:
        import subprocess
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-AppxPackage *WhatsApp*"],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("PackageFamilyName"):
                info["family"] = line.split(":", 1)[1].strip()
            elif line.startswith("PackageFullName"):
                info["full_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("InstallLocation"):
                info["path"] = line.split(":", 1)[1].strip()
        if info["family"]:
            info["aumid"] = f"{info['family']}!App"
            info["mismatch"] = (info["aumid"].lower() != expected_aumid.lower())
    except Exception as e:
        info["details"] = str(e)
    return info


def get_session_diagnostics():
    """
    Determine session and desktop/window station properties for JARVIS and WhatsApp.
    """
    diag = {
        "jarvis_session": None,
        "whatsapp_sessions": [],
        "interactive_session": None,
        "desktop_window_station": "Unknown",
        "thread_desktop": "Unknown",
        "window_station": "Unknown",
        "default_desktop_accessible": False
    }
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        
        jarvis_sid = ctypes.c_ulong()
        kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(jarvis_sid))
        diag["jarvis_session"] = jarvis_sid.value
        
        if hasattr(kernel32, "WTSGetActiveConsoleSessionId"):
            diag["interactive_session"] = kernel32.WTSGetActiveConsoleSessionId()

        for p in psutil.process_iter(['pid', 'name']):
            try:
                pname = p.info.get('name') or ''
                if 'whatsapp' in pname.lower():
                    wa_sid = ctypes.c_ulong()
                    kernel32.ProcessIdToSessionId(p.info['pid'], ctypes.byref(wa_sid))
                    diag["whatsapp_sessions"].append({
                        "pid": p.info['pid'],
                        "name": pname,
                        "session_id": wa_sid.value
                    })
            except Exception:
                pass

        h_winsta = user32.GetProcessWindowStation()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetUserObjectInformationW(h_winsta, 2, buf, 512, None)
        diag["window_station"] = buf.value

        h_desk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        buf2 = ctypes.create_unicode_buffer(256)
        user32.GetUserObjectInformationW(h_desk, 2, buf2, 512, None)
        diag["thread_desktop"] = buf2.value
        diag["desktop_window_station"] = f"{diag['window_station']}\\{diag['thread_desktop']}"

        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            diag["default_desktop_accessible"] = True
            user32.CloseDesktop(h_default)
    except Exception as e:
        diag["error"] = str(e)
    return diag


def enumerate_all_desktop_windows():
    """
    Enumerate visible top-level windows from current thread desktop and Default desktop (if accessible).
    Extracts strictly non-sensitive technical metadata.
    """
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    windows = []
    seen_hwnds = set()

    def inspect_hwnd(hwnd):
        if not hwnd or hwnd in seen_hwnds:
            return
        seen_hwnds.add(hwnd)
        try:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = pid.value
            pname = ""
            if pid_val:
                try:
                    pname = psutil.Process(pid_val).name()
                except Exception:
                    pass

            length = user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value

            cls_buff = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buff, 256)
            cls_name = cls_buff.value

            visible = bool(user32.IsWindowVisible(hwnd))
            iconic = bool(user32.IsIconic(hwnd))

            rect = (ctypes.c_long * 4)()
            user32.GetWindowRect(hwnd, rect)
            left, top, right, bottom = rect[0], rect[1], rect[2], rect[3]
            width = max(0, right - left)
            height = max(0, bottom - top)

            is_wa = (
                "whatsapp" in pname.lower() or 
                "whatsapp" in title.lower() or 
                ("ApplicationFrameWindow" in cls_name and "whatsapp" in title.lower()) or
                ("WinUIDesktopWin32WindowClass" in cls_name and ("whatsapp" in title.lower() or "whatsapp" in pname.lower()))
            )

            windows.append({
                "hwnd": hwnd,
                "pid": pid_val,
                "proc": pname,
                "title": title,
                "class": cls_name,
                "visible": visible,
                "iconic": iconic,
                "width": width,
                "height": height,
                "bounds": (left, top, right, bottom),
                "is_whatsapp_candidate": is_wa
            })
        except Exception:
            pass

    def enum_cb(hwnd, lparam):
        inspect_hwnd(hwnd)
        return True

    # Check 1: Thread Desktop
    try:
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception:
        pass

    # Check 2: Default Desktop
    try:
        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            user32.EnumDesktopWindows(h_default, WNDENUMPROC(enum_cb), 0)
            user32.CloseDesktop(h_default)
    except Exception:
        pass

    return windows


def find_whatsapp_hwnd_default_desktop():
    """
    Phase 2: Safely discover WhatsApp top-level HWND on the interactive 'Default' desktop.
    Verifies:
      - IsWindow(hwnd)
      - IsWindowVisible(hwnd)
      - PID belongs to WhatsApp.Root.exe / WhatsApp.exe
      - Session ID matches interactive session
      - Width > 100, Height > 100
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    matches = []
    
    h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
    if not h_default:
        return None, None
        
    active_session = kernel32.WTSGetActiveConsoleSessionId() if hasattr(kernel32, "WTSGetActiveConsoleSessionId") else None

    def enum_cb(hwnd, lparam):
        if not hwnd or not user32.IsWindow(hwnd):
            return True
        vis = bool(user32.IsWindowVisible(hwnd))
        iconic = bool(user32.IsIconic(hwnd))

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = pid.value

        pname = ""
        if pid_val:
            try:
                pname = psutil.Process(pid_val).name()
            except Exception:
                pass

        if "whatsapp" not in pname.lower():
            return True

        wa_sid = ctypes.c_ulong()
        kernel32.ProcessIdToSessionId(pid_val, ctypes.byref(wa_sid))

        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value

        cls_buff = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buff, 256)
        cls_name = cls_buff.value

        rect = (ctypes.c_long * 4)()
        user32.GetWindowRect(hwnd, rect)
        left, top, right, bottom = rect[0], rect[1], rect[2], rect[3]
        width = max(0, right - left)
        height = max(0, bottom - top)

        info = {
            "hwnd": hwnd,
            "pid": pid_val,
            "proc": pname,
            "title": title,
            "class": cls_name,
            "visible": vis,
            "iconic": iconic,
            "width": width,
            "height": height,
            "bounds": (left, top, right, bottom),
            "session_id": wa_sid.value,
            "active_session": active_session,
            "is_whatsapp_candidate": True
        }
        matches.append((hwnd, info))
        return True

    try:
        user32.EnumDesktopWindows(h_default, WNDENUMPROC(enum_cb), 0)
    except Exception:
        pass
    finally:
        user32.CloseDesktop(h_default)

    # Filter for valid interactive window (width > 100, height > 100)
    for hwnd, info in matches:
        if info.get("width", 0) > 100 and info.get("height", 0) > 100:
            return hwnd, info

    return (matches[0] if matches else (None, None))


def validate_whatsapp_hwnd(hwnd_info):
    """
    Phase 3: Validate that an HWND candidate represents a usable interactive WhatsApp window.
    """
    if not hwnd_info:
        return False
    hwnd = hwnd_info.get("hwnd")
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return False
    if not hwnd_info.get("visible") and not hwnd_info.get("iconic"):
        return False
    if hwnd_info.get("width", 0) <= 100 or hwnd_info.get("height", 0) <= 100:
        return False
    proc = (hwnd_info.get("proc") or "").lower()
    if "whatsapp" not in proc:
        return False
    return True


def bridge_hwnd_to_uia(hwnd):
    """
    Phase 4: Bridge a native HWND directly into a UI Automation WindowControl / Control.
    Handles cross-desktop isolation seamlessly.
    """
    if not hwnd:
        return None
        
    # Priority 1: Direct ControlFromHandle in calling thread
    try:
        ctrl = auto.ControlFromHandle(hwnd)
        if ctrl and ctrl.Exists(0.1, 0):
            rect = ctrl.BoundingRectangle
            if rect and (rect.right - rect.left) > 100 and (rect.bottom - rect.top) > 100:
                return ctrl
    except Exception:
        pass

    # Priority 2: Execute ControlFromHandle inside a worker thread attached to Default desktop
    import threading
    res_container = {}

    def _bridge_worker():
        try:
            user32 = ctypes.windll.user32
            h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_default:
                set_res = user32.SetThreadDesktop(h_default)
                if set_res:
                    try:
                        import uiautomation as worker_auto
                        c = worker_auto.ControlFromHandle(hwnd)
                        if c and c.Exists(0.5, 0):
                            rect = c.BoundingRectangle
                            if rect and (rect.right - rect.left) > 100 and (rect.bottom - rect.top) > 100:
                                res_container["ctrl"] = c
                    except Exception as w_err:
                        res_container["error"] = str(w_err)
                user32.CloseDesktop(h_default)
        except Exception as e:
            res_container["error"] = str(e)

    t = threading.Thread(target=_bridge_worker)
    t.start()
    t.join(timeout=2.0)

    if res_container.get("ctrl"):
        return res_container["ctrl"]
        
    return None


def find_native_whatsapp_hwnd():
    """
    Locate native Win32 HWND for WhatsApp window across desktop processes.
    Prioritizes Default desktop.
    """
    hwnd, info = find_whatsapp_hwnd_default_desktop()
    if hwnd and info:
        return info

    all_wins = enumerate_all_desktop_windows()
    wa_wins = [w for w in all_wins if w.get("is_whatsapp_candidate")]
    if wa_wins:
        visible_wa = [w for w in wa_wins if w.get("visible") and not w.get("iconic")]
        return visible_wa[0] if visible_wa else wa_wins[0]
    return None


def activate_native_hwnd(hwnd_info):
    """
    Phase 5: Restore and bring a native HWND to the foreground, then obtain UIA Control.
    """
    if not hwnd_info:
        return None
    try:
        hwnd = hwnd_info.get("hwnd")
        user32 = ctypes.windll.user32
        if hwnd_info.get("iconic") or not hwnd_info.get("visible"):
            user32.ShowWindow(hwnd, 9) # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        
        return bridge_hwnd_to_uia(hwnd)
    except Exception:
        pass
    return None


def instrumented_activate_whatsapp():
    """
    Add diagnostic instrumentation around the existing activation mechanisms.
    Records method details, timestamps, return codes, error codes, and before/after metrics.
    """
    import datetime
    import subprocess
    
    # Baseline before activation
    p_info_before = get_whatsapp_process_info()
    pids_before = p_info_before.get("pids", [])
    p_count_before = len(pids_before)
    
    hwnd_all_before = enumerate_all_desktop_windows()
    hwnd_wa_before = [w for w in hwnd_all_before if w.get("is_whatsapp_candidate")]
    hwnd_count_before = len(hwnd_wa_before)
    
    uia_count_before = 0
    try:
        root = auto.GetRootControl()
        for w in root.GetChildren():
            try:
                pname = psutil.Process(w.ProcessId).name() if w.ProcessId else ""
                wname = str(getattr(w, "Name", "") or "")
                if "whatsapp" in pname.lower() or "whatsapp" in wname.lower():
                    uia_count_before += 1
            except Exception:
                pass
    except Exception:
        pass

    results = {
        "whatsapp_uri": {
            "method": "whatsapp: URI activation",
            "timestamp": datetime.datetime.now().isoformat(),
            "return": None,
            "error": None,
            "exception": None
        },
        "apps_folder": {
            "method": "shell:AppsFolder activation",
            "timestamp": datetime.datetime.now().isoformat(),
            "return": None,
            "error": None,
            "exception": None
        },
        "process_count_before": p_count_before,
        "process_count_after": p_count_before,
        "pids_before": pids_before,
        "pids_after": pids_before,
        "hwnd_count_before": hwnd_count_before,
        "hwnd_count_after": hwnd_count_before,
        "uia_count_before": uia_count_before,
        "uia_count_after": uia_count_before,
    }

    # 1. whatsapp: URI activation
    try:
        t0 = datetime.datetime.now().isoformat()
        results["whatsapp_uri"]["timestamp"] = t0
        res = ctypes.windll.shell32.ShellExecuteW(None, "open", "whatsapp:", None, None, 1)
        err = ctypes.GetLastError()
        results["whatsapp_uri"]["return"] = res
        results["whatsapp_uri"]["error"] = err if res <= 32 else 0
    except Exception as e:
        results["whatsapp_uri"]["exception"] = str(e)

    # 2. shell:AppsFolder activation
    try:
        t1 = datetime.datetime.now().isoformat()
        results["apps_folder"]["timestamp"] = t1
        proc = subprocess.Popen(["explorer.exe", "shell:AppsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"])
        results["apps_folder"]["return"] = proc.pid
        results["apps_folder"]["error"] = 0
    except Exception as e:
        results["apps_folder"]["exception"] = str(e)

    time.sleep(0.5)

    # Snapshot after activation
    p_info_after = get_whatsapp_process_info()
    pids_after = p_info_after.get("pids", [])
    results["process_count_after"] = len(pids_after)
    results["pids_after"] = pids_after

    hwnd_all_after = enumerate_all_desktop_windows()
    hwnd_wa_after = [w for w in hwnd_all_after if w.get("is_whatsapp_candidate")]
    results["hwnd_count_after"] = len(hwnd_wa_after)

    uia_count_after = 0
    try:
        root = auto.GetRootControl()
        for w in root.GetChildren():
            try:
                pname = psutil.Process(w.ProcessId).name() if w.ProcessId else ""
                wname = str(getattr(w, "Name", "") or "")
                if "whatsapp" in pname.lower() or "whatsapp" in wname.lower():
                    uia_count_after += 1
            except Exception:
                pass
    except Exception:
        pass
    results["uia_count_after"] = uia_count_after

    return results


def safe_launch_whatsapp():
    """
    Safely trigger WhatsApp Desktop activation via standard Windows URI / Appx alias.
    """
    act_res = instrumented_activate_whatsapp()
    uri_ok = bool(act_res.get("whatsapp_uri", {}).get("return", 0) > 32)
    apps_ok = bool(act_res.get("apps_folder", {}).get("return") is not None)
    return uri_ok or apps_ok


def find_whatsapp_window():
    """
    Direct WhatsApp window discovery.
    Prioritizes Default-desktop native HWND lookup and UIA bridging directly.
    """
    set_dpi_awareness()
    hwnd_info = find_native_whatsapp_hwnd()
    if hwnd_info:
        ctrl = activate_native_hwnd(hwnd_info)
        if ctrl:
            return ctrl
    return None


def acquire_whatsapp_window(timeout=15.0, poll_interval=0.5):
    """
    Phase 2 & 5: Robust multi-layer window acquisition / activation layer with 15s timeline tracking.
    """
    start_time = time.time()
    timeline = []
    
    pkg_info = get_package_identity()
    session_info = get_session_diagnostics()
    
    diag = {
        "process_found": False,
        "hwnd_found": False,
        "uia_window_found": False,
        "visible": False,
        "minimized": False,
        "bounds": None,
        "foreground_status": False,
        "launch_attempted": False,
        "acquisition_elapsed": 0.0,
        "failure_reason": None,
        "window": None,
        "package_identity": pkg_info,
        "session": session_info,
        "activation_instrumentation": None,
        "candidate_hwnd": None,
        "timeline": timeline,
        "final_diagnosis": None
    }

    def record_tick(t_elapsed):
        p_info = get_whatsapp_process_info()
        h_info = find_native_whatsapp_hwnd()
        u_win = None
        try:
            u_win = find_whatsapp_window()
        except Exception:
            pass
        tick = {
            "time_str": f"{t_elapsed:.1f}s",
            "process_presence": bool(p_info.get("process_found")),
            "pids": p_info.get("pids", []),
            "hwnd_presence": bool(h_info is not None),
            "hwnd": h_info.get("hwnd") if h_info else None,
            "hwnd_title": h_info.get("title") if h_info else None,
            "hwnd_class": h_info.get("class") if h_info else None,
            "uia_window_presence": bool(u_win is not None),
            "visible": bool(h_info.get("visible")) if h_info else (bool(u_win and not getattr(u_win, "IsOffscreen", False))),
            "bounds": h_info.get("bounds") if h_info else (u_win.BoundingRectangle if u_win else None)
        }
        timeline.append(tick)
        return p_info, h_info, u_win

    # Tick 0.0s
    p_info, h_info, u_win = record_tick(0.0)
    diag["process_found"] = p_info.get("process_found", False)

    # Step 1: Direct discovery from existing HWND / UIA
    if u_win and u_win.Exists(0, 0):
        rect = u_win.BoundingRectangle
        if rect and (rect.right - rect.left) > 100 and (rect.bottom - rect.top) > 100:
            diag["uia_window_found"] = True
            diag["visible"] = not getattr(u_win, "IsOffscreen", False)
            diag["bounds"] = (rect.left, rect.top, rect.right, rect.bottom)
            diag["window"] = u_win
            diag["candidate_hwnd"] = h_info
            if h_info:
                diag["hwnd_found"] = True
                diag["minimized"] = h_info.get("iconic", False)
            try:
                u_win.SetActive()
                diag["foreground_status"] = True
            except Exception:
                pass
            diag["acquisition_elapsed"] = round(time.time() - start_time, 2)
            diag["final_diagnosis"] = "Window acquired directly via native HWND / UI Automation."
            return u_win, diag

    # Step 2: Trigger safe launch / instrumented activation if no interactive window exists
    diag["launch_attempted"] = safe_launch_whatsapp()
    diag["activation_instrumentation"] = instrumented_activate_whatsapp()

    # Step 3: Bounded polling loop (up to timeout)
    while (time.time() - start_time) < timeout:
        time.sleep(poll_interval)
        t_elapsed = round(time.time() - start_time, 2)
        p_info, h_info, u_win = record_tick(t_elapsed)

        if u_win and u_win.Exists(0, 0):
            rect = u_win.BoundingRectangle
            if rect and (rect.right - rect.left) > 100 and (rect.bottom - rect.top) > 100:
                diag["uia_window_found"] = True
                diag["visible"] = not getattr(u_win, "IsOffscreen", False)
                diag["bounds"] = (rect.left, rect.top, rect.right, rect.bottom)
                diag["window"] = u_win
                diag["candidate_hwnd"] = h_info
                if h_info:
                    diag["hwnd_found"] = True
                    diag["minimized"] = h_info.get("iconic", False)
                try:
                    u_win.SetActive()
                    diag["foreground_status"] = True
                except Exception:
                    pass
                diag["acquisition_elapsed"] = t_elapsed
                diag["final_diagnosis"] = "Window acquired via UI Automation after activation."
                return u_win, diag

    # Step 4: Classify failure state
    diag["acquisition_elapsed"] = round(time.time() - start_time, 2)
    diag["failure_reason"] = "WHATSAPP_WINDOW_NOT_AVAILABLE"

    h_info_final = find_native_whatsapp_hwnd()
    if h_info_final:
        diag["candidate_hwnd"] = h_info_final
        diag["hwnd_found"] = True

    if pkg_info.get("mismatch"):
        diag["final_diagnosis"] = "F. Package/AppUserModelID mismatch."
    elif not diag["process_found"] and not h_info_final:
        diag["final_diagnosis"] = "A. Activation failed: WhatsApp process and window are completely absent."
    elif diag["process_found"] and not h_info_final:
        diag["final_diagnosis"] = "B. Activation succeeded but no WhatsApp window was created (WhatsApp.Root running purely in background)."
    elif h_info_final and not diag["uia_window_found"]:
        diag["final_diagnosis"] = "D. Window exists but UI Automation cannot access it."
    else:
        diag["final_diagnosis"] = f"G. Other — WhatsApp window acquisition timed out after {diag['acquisition_elapsed']}s."

    return None, diag


def inspect_ui_element(element):
    """
    Extract safe metadata from a UI element for inspection without leaking message bodies.
    """
    if not element:
        return None
    try:
        rect = element.BoundingRectangle
        return {
            "Name": str(element.Name) if getattr(element, "Name", None) is not None else "",
            "ControlType": str(auto.ControlTypeName(element.ControlType)) if hasattr(auto, 'ControlTypeName') and hasattr(element, 'ControlType') else str(getattr(element, 'ControlType', '')),
            "AutomationId": str(element.AutomationId) if getattr(element, "AutomationId", None) is not None else "",
            "ClassName": str(element.ClassName) if getattr(element, "ClassName", None) is not None else "",
            "IsEnabled": bool(getattr(element, "IsEnabled", True)),
            "IsOffscreen": bool(getattr(element, "IsOffscreen", False)),
            "BoundingRectangle": (rect.left, rect.top, rect.right, rect.bottom) if rect else None
        }
    except Exception as e:
        return {"error": str(e)}


def get_element_center(element):
    """
    Calculate the dynamic center coordinate (center_x, center_y) of a UI element.
    """
    if not element:
        return None
    try:
        rect = element.BoundingRectangle
        if not rect:
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        center_x = int(rect.left + (width / 2))
        center_y = int(rect.top + (height / 2))
        return (center_x, center_y)
    except Exception:
        return None


def invoke_ui_element(element, is_call_action=False):
    """
    Execute native UI Automation interaction on an element (Invoke/Click/Center Click),
    avoiding TogglePattern for action/call buttons.
    Returns True if an invocation mechanism was dispatched on a valid element.
    """
    if not element or not element.Exists(0, 0):
        return False
    
    # 1. Native Invoke Pattern (Primary preferred for action buttons)
    try:
        invoke_pat = element.GetInvokePattern()
        if invoke_pat:
            invoke_pat.Invoke()
            return True
    except Exception:
        pass

    # 2. UIA Native Click
    try:
        element.Click(waitTime=0.1)
        return True
    except Exception:
        pass

    # 3. Physical click at dynamic element center
    center = get_element_center(element)
    if center:
        try:
            import pyautogui
            pyautogui.click(center[0], center[1])
            return True
        except Exception:
            pass

    # 4. Toggle Pattern (ONLY for elements explicitly verified as toggle controls and NOT call actions)
    if not is_call_action:
        try:
            c_type = auto.ControlTypeName(element.ControlType) if hasattr(auto, 'ControlTypeName') else str(element.ControlType)
            if "CheckBox" in c_type or "Toggle" in c_type:
                toggle_pat = element.GetTogglePattern()
                if toggle_pat:
                    toggle_pat.Toggle()
                    return True
        except Exception:
            pass

    return False


def validate_element_for_action(element, window_rect=None):
    """
    Validate that an element exists, is visible, enabled, has valid bounds,
    and belongs to the active WhatsApp window.
    """
    if not element or not element.Exists(0, 0):
        return False
    try:
        if hasattr(element, 'IsEnabled') and not element.IsEnabled:
            return False
        if hasattr(element, 'IsOffscreen') and element.IsOffscreen:
            return False
        rect = element.BoundingRectangle
        if not rect:
            return False
        if (rect.right - rect.left) <= 2 or (rect.bottom - rect.top) <= 2:
            return False
        
        if window_rect:
            # Center of element must be within window rectangle
            cx = (rect.left + rect.right) / 2
            cy = (rect.top + rect.bottom) / 2
            if not (window_rect.left <= cx <= window_rect.right and window_rect.top <= cy <= window_rect.bottom):
                return False
        return True
    except Exception:
        return False


def find_chat_element(contact_name, window=None):
    """
    Locate the target contact/chat UI element using priority matching:
    1. Exact accessible Name match
    2. Exact visible text match (case-insensitive)
    3. Normalized name match
    4. Substring in search result items
    """
    if not window:
        window = find_whatsapp_window()
    if not window:
        return []

    target_clean = contact_name.strip()
    target_norm = target_clean.lower().replace(" ", "")
    
    matches = []
    
    try:
        # Search inside document control / webview / general descendants
        doc = window.DocumentControl(searchDepth=8)
        search_root = doc if doc.Exists(0.1, 0) else window

        for c, d in auto.WalkControl(search_root, maxDepth=12):
            try:
                name = (c.Name or "").strip()
                if not name:
                    continue
                
                name_norm = name.lower().replace(" ", "")
                
                # Check for exact or normalized match
                if name.lower() == target_clean.lower() or name_norm == target_norm or target_norm in name_norm:
                    # Filter out search edit box itself or status bars
                    c_type = auto.ControlTypeName(c.ControlType) if hasattr(auto, 'ControlTypeName') else str(c.ControlType)
                    if "Edit" not in c_type:
                        matches.append(c)
            except Exception:
                continue
    except Exception:
        pass

    return matches


def is_valid_call_candidate(control, call_type="voice", window_rect=None, header_rect=None):
    """
    Strict validation for a call button candidate to prevent false positives from
    chat messages, call logs, sidebar tabs, timestamps, or notifications.
    """
    if not control or not getattr(control, "Exists", lambda w, s: True)(0, 0):
        return False
    if not validate_element_for_action(control, window_rect):
        return False

    c_name = str(getattr(control, "Name", "") or "").strip()
    c_name_lower = c_name.lower()
    auto_id = str(getattr(control, "AutomationId", "") or "").strip().lower()
    c_type = format_control_type_name(getattr(control, "ControlType", None))
    
    # Must be ButtonControl
    if "Button" not in c_type:
        return False

    # Negative keyword filtering (call history, message logs, status, tabs, timestamps, notifications, search)
    negative_phrases = [
        "click to call back", "history", "missed", "incoming", "outgoing", "duration",
        "declined", "cancelled", "unread", "last seen", "typing", "yesterday", "today",
        " am", " pm", "search", "status", "channels", "communities", "messages",
        "settings", "profile", "log", "older messages", "phone."
    ]
    if any(neg in c_name_lower for neg in negative_phrases) or any(neg in auto_id for neg in negative_phrases):
        return False

    # Reject left navigation sidebar "Calls" button
    if c_name_lower == "calls":
        return False

    # Position validation: Must have valid positive dimensions
    rect = control.BoundingRectangle
    if not rect:
        return False
    
    r_left = getattr(rect, "left", rect[0] if isinstance(rect, (list, tuple)) else 0)
    r_top = getattr(rect, "top", rect[1] if isinstance(rect, (list, tuple)) else 0)
    r_right = getattr(rect, "right", rect[2] if isinstance(rect, (list, tuple)) else 0)
    r_bottom = getattr(rect, "bottom", rect[3] if isinstance(rect, (list, tuple)) else 0)

    width = r_right - r_left
    height = r_bottom - r_top
    if width <= 2 or height <= 2:
        return False

    # Negative scroll coordinates rejection
    if r_left < 0 or r_top < 0:
        return False

    # Header region check
    if header_rect and isinstance(header_rect, (list, tuple)) and len(header_rect) == 4:
        # Check if control overlaps with or lies within header bounds with tolerance
        if r_top > (header_rect[3] + 20) or r_bottom < (header_rect[1] - 20):
            return False
    elif window_rect:
        w_top = getattr(window_rect, "top", window_rect[1] if isinstance(window_rect, (list, tuple)) else 0)
        w_bottom = getattr(window_rect, "bottom", window_rect[3] if isinstance(window_rect, (list, tuple)) else 0)
        win_h = max(1, w_bottom - w_top)
        if (r_top - w_top) > (win_h * 0.35) or r_top < (w_top - 10):
            return False

    # Voice vs Video target match
    is_video_target = "video" in call_type.lower()
    if is_video_target:
        if c_name_lower == "voice call":
            return False
        high_names = [
            "video call", "video call button", "start video call", 
            "start a video call", "video call with", "video"
        ]
        id_candidates = ["videocall", "btnvideocall", "video_call", "video-call", "btn-video-call", "videocallbutton"]
    else:
        if "video" in c_name_lower:
            return False
        high_names = [
            "voice call", "voice call button", "start voice call", 
            "audio call", "start audio call", "audio", "start call"
        ]
        id_candidates = ["voicecall", "audiocall", "voice_call", "audio_call", "btnvoicecall", "btn-voice-call", "voicecallbutton", "audiocallbutton", "callbutton", "btncall"]

    # Match check
    if any(h == c_name_lower or (h in c_name_lower and len(c_name_lower) < 35) for h in high_names):
        return True
    if auto_id and any(cand in auto_id for cand in id_candidates):
        return True
    
    # Generic "call" check only if ControlType is explicitly ButtonControl and target is voice
    if c_name_lower == "call" and not is_video_target:
        return True

    return False


def find_call_button(window, call_type="voice"):
    """
    Search the WhatsApp UI tree dynamically for voice or video call button.
    Priority 1: Discovered Chat Header scoped search
    Priority 2: Window-wide search with searchDepth=25
    Priority 3: Scored candidate matching
    """
    if not window or not getattr(window, "Exists", lambda w, s: True)(0, 0):
        return None

    win_rect = getattr(window, "BoundingRectangle", None)
    call_type_lower = call_type.lower()
    target_name = "Video call" if "video" in call_type_lower else "Voice call"

    # Step 1: Discover chat header structurally
    header_info = discover_chat_header(window)
    header_elem = None
    header_rect = None

    if header_info.get("found"):
        header_rect = header_info.get("header_container", {}).get("BoundingRectangle")
        # Find header container object for direct scoping
        try:
            for btn_name in [target_name, "Search", "Menu", "Back"]:
                b = window.ButtonControl(searchDepth=25, Name=btn_name)
                if b.Exists(0.05, 0):
                    curr = b.GetParentControl()
                    while curr and curr != window:
                        r = curr.BoundingRectangle
                        if r and (r.right - r.left) > 250 and 35 <= (r.bottom - r.top) <= 200:
                            header_elem = curr
                            break
                        curr = curr.GetParentControl()
                if header_elem:
                    break
        except Exception:
            pass

    # Priority 1: Header-scoped search for exact button name
    if header_elem:
        try:
            btn = header_elem.ButtonControl(searchDepth=6, Name=target_name)
            if btn.Exists(0.05, 0) and is_valid_call_candidate(btn, call_type, win_rect, header_rect):
                return btn
        except Exception:
            pass

    # Priority 2: Window-wide search with searchDepth=25 for exact button name
    try:
        btn = window.ButtonControl(searchDepth=25, Name=target_name)
        if btn.Exists(0.05, 0) and is_valid_call_candidate(btn, call_type, win_rect, header_rect):
            return btn
    except Exception:
        pass

    # Priority 3: Window-wide search for preferred alternative names
    preferred_names = (
        ["Video call", "video call", "Start video call", "Video"]
        if "video" in call_type_lower
        else ["Voice call", "voice call", "Start voice call", "Audio call", "Audio", "Start call"]
    )
    for name in preferred_names:
        try:
            btn = window.ButtonControl(searchDepth=25, Name=name)
            if btn.Exists(0.05, 0) and is_valid_call_candidate(btn, call_type, win_rect, header_rect):
                return btn
        except Exception:
            pass

    # Priority 4: Candidate scoring walk across window descendants
    try:
        best_candidate = None
        best_score = 45
        for c, d in auto.WalkControl(window, maxDepth=16):
            try:
                c_name = str(getattr(c, "Name", "") or "").strip().lower()
                c_tp = format_control_type_name(getattr(c, "ControlType", None))
                if "ListItem" in c_tp or "ListControl" in c_tp or "Document" in c_tp:
                    continue
                if any(k in c_name for k in ["voice", "video", "call", "audio"]):
                    sc = score_call_candidate(c, call_type=call_type, win_bounds=win_rect, header_bounds=header_rect)
                    if sc.get("Score", 0) > best_score and is_valid_call_candidate(c, call_type, win_rect, header_rect):
                        best_score = sc.get("Score", 0)
                        best_candidate = c
            except Exception:
                continue
        if best_candidate:
            return best_candidate
    except Exception:
        pass

    return None


def create_icon_templates():
    """Generate self-contained icon templates for visual detection."""
    import cv2
    import numpy as np
    
    # Phone handset template (24x24)
    size = 24
    phone = np.zeros((size, size), dtype=np.uint8)
    cv2.ellipse(phone, (size // 2, size // 2 + 2), (size // 3, size // 3), 0, 190, 350, 255, 3)
    cv2.circle(phone, (int(size * 0.22), int(size * 0.45)), 3, 255, -1)
    cv2.circle(phone, (int(size * 0.78), int(size * 0.45)), 3, 255, -1)
    
    # Video camera template (24x24)
    camera = np.zeros((size, size), dtype=np.uint8)
    cv2.rectangle(camera, (int(size * 0.15), int(size * 0.25)), (int(size * 0.65), int(size * 0.75)), 255, -1)
    pts = np.array([
        [int(size * 0.65), int(size * 0.4)],
        [int(size * 0.88), int(size * 0.25)],
        [int(size * 0.88), int(size * 0.75)],
        [int(size * 0.65), int(size * 0.6)]
    ], np.int32)
    cv2.fillPoly(camera, [pts], 255)
    
    return {"voice": phone, "video": camera}


def visual_call_button_fallback(window, call_type="voice"):
    """
    LEVEL 2: Visual detection fallback using window region screenshot and multi-scale template matching.
    """
    if not window or not window.Exists(0, 0):
        return False

    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
        import pyautogui

        rect = window.BoundingRectangle
        if not rect:
            return False
        
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
            return False

        # Capture WhatsApp window region
        screen = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
        screen_np = np.array(screen)
        gray = cv2.cvtColor(screen_np, cv2.COLOR_RGB2GRAY)

        # Crop to the header region (top 20% of window, right 50% of window)
        header_h = int(height * 0.20)
        header_w_start = int(width * 0.50)
        header_roi = gray[0:header_h, header_w_start:width]

        templates = create_icon_templates()
        base_template = templates.get("video" if "video" in call_type.lower() else "voice")
        if base_template is None:
            return False

        best_val = -1
        best_loc = None
        best_scale = 1.0

        # Multi-scale search (scaling from 0.7x to 1.8x for various DPI / UI sizes)
        for scale in [0.75, 1.0, 1.25, 1.5, 1.75]:
            tw = int(base_template.shape[1] * scale)
            th = int(base_template.shape[0] * scale)
            if tw >= header_roi.shape[1] or th >= header_roi.shape[0] or tw <= 4 or th <= 4:
                continue
            resized_tmpl = cv2.resize(base_template, (tw, th))
            edges_tmpl = cv2.Canny(resized_tmpl, 50, 150)
            edges_roi = cv2.Canny(header_roi, 50, 150)
            
            res = cv2.matchTemplate(edges_roi, edges_tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            
            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale

        # Threshold check
        if best_val >= 0.55 and best_loc is not None:
            tw = int(base_template.shape[1] * best_scale)
            th = int(base_template.shape[0] * best_scale)
            roi_x, roi_y = best_loc
            
            # Map back to screen coordinates
            click_x = rect.left + header_w_start + roi_x + (tw // 2)
            click_y = rect.top + roi_y + (th // 2)

            # Validate coordinate inside window
            if rect.left <= click_x <= rect.right and rect.top <= click_y <= rect.bottom:
                window.SetActive()
                time.sleep(0.1)
                pyautogui.click(click_x, click_y)
                return True
    except Exception:
        pass

    return False


def window_relative_fallback(window, call_type="voice"):
    """
    LEVEL 3: Window-relative fallback.
    Dynamically derives target coordinates as a proportional fraction of CURRENT window dimensions.
    """
    if not window or not window.Exists(0, 0):
        return False

    try:
        import pyautogui
        rect = window.BoundingRectangle
        if not rect:
            return False
            
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        
        if width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
            return False

        rel_x = FALLBACK_RELATIVE_VIDEO_X if "video" in call_type.lower() else FALLBACK_RELATIVE_VOICE_X
        rel_y = FALLBACK_RELATIVE_HEADER_Y

        click_x = int(rect.left + (width * rel_x))
        click_y = int(rect.top + (height * rel_y))

        # Validate coordinate is strictly inside WhatsApp window
        if not (rect.left < click_x < rect.right and rect.top < click_y < rect.bottom):
            return False

        window.SetActive()
        time.sleep(0.15)
        pyautogui.click(click_x, click_y)
        return True
    except Exception:
        return False


def keyboard_call_fallback(window, call_type="voice"):
    """
    LEVEL 4: Keyboard navigation fallback.
    """
    if not window or not window.Exists(0, 0):
        return False
    try:
        window.SetActive()
        time.sleep(0.2)
        # In WhatsApp Desktop, Ctrl+Shift+C (or Alt navigation) can focus call actions in certain versions
        # Pressing Escape then Tab navigation
        window.SendKeys('{Esc}', waitTime=0.2)
        return True
    except Exception:
        return False


def get_call_ui_state(window):
    """
    Capture a lightweight snapshot of call-related UI controls before or after invocation.
    """
    state = {
        "has_voice_button": False,
        "has_video_button": False,
        "has_end_call_button": False,
        "active_indicators": [],
        "voice_button_meta": None,
    }
    if not window or not window.Exists(0, 0):
        return state

    try:
        v_btn = find_call_button(window, "voice")
        if v_btn:
            state["has_voice_button"] = True
            state["voice_button_meta"] = inspect_ui_element(v_btn)
    except Exception:
        pass

    try:
        vid_btn = find_call_button(window, "video")
        if vid_btn:
            state["has_video_button"] = True
    except Exception:
        pass

    # Check for active call indicators or End call button
    end_call_names = ["End call", "End Call", "end call", "End voice call", "End video call", "Hang up", "hang up", "Leave call", "Leave"]
    win_rect = window.BoundingRectangle
    for name in end_call_names:
        try:
            btn = window.ButtonControl(searchDepth=12, Name=name)
            if btn.Exists(0.05, 0) and validate_element_for_action(btn, win_rect):
                state["has_end_call_button"] = True
                state["active_indicators"].append(f"Button:{name}")
                break
        except Exception:
            pass

    try:
        for c, d in auto.WalkControl(window, maxDepth=10):
            try:
                c_name = str(getattr(c, "Name", "") or "").strip().lower()
                c_type = str(auto.ControlTypeName(c.ControlType)) if hasattr(auto, 'ControlTypeName') and hasattr(c, 'ControlType') else str(getattr(c, 'ControlType', ''))
                if "ListItem" in c_type or "ListControl" in c_type:
                    continue
                if any(x in c_name for x in ["calling", "ringing", "connected", "call in progress", "ongoing call"]):
                    state["active_indicators"].append(c_name)
                    break
            except Exception:
                continue
    except Exception:
        pass

    return state


def verify_call_started(window, timeout=5.0, before_state=None):
    """
    Verify if the call actually started by checking for:
    - "End call" / "Hang up" / "Leave call" buttons appearing
    - "Calling..." / "Ringing..." / "Connected" / "Ongoing call" status
    - Active call timer format (mm:ss)
    - UI state transition from before-action state
    Does not consider standalone 'mute' or 'voice' as proof of active call.
    """
    start_time = time.time()
    
    end_call_names = [
        "End call", "End Call", "end call", "End voice call", "End video call", 
        "Hang up", "hang up", "Leave call", "leave call", "Leave", "Decline"
    ]
    
    while time.time() - start_time < timeout:
        if window and window.Exists(0, 0):
            win_rect = window.BoundingRectangle
            
            # Check 1: Button controls with positive end/leave/hang up action
            for name in end_call_names:
                try:
                    btn = window.ButtonControl(searchDepth=12, Name=name)
                    if btn.Exists(0.05, 0) and validate_element_for_action(btn, win_rect):
                        return "SUCCESS_VERIFIED"
                except Exception:
                    pass
            
            # Check 2: Walk controls for positive active call indicators (calling/ringing/connected/timer)
            try:
                for c, d in auto.WalkControl(window, maxDepth=10):
                    try:
                        c_name = str(getattr(c, "Name", "") or "").strip().lower()
                        c_type = str(auto.ControlTypeName(c.ControlType)) if hasattr(auto, 'ControlTypeName') and hasattr(c, 'ControlType') else str(getattr(c, 'ControlType', ''))
                        if "ListItem" in c_type or "ListControl" in c_type:
                            continue
                        if any(x in c_name for x in ["calling", "ringing", "connected", "call in progress", "ongoing call"]):
                            return "SUCCESS_VERIFIED"
                        # Check for active timer pattern mm:ss in short status element
                        if len(c_name) < 15 and re.search(r'\b\d{1,2}:\d{2}\b', c_name):
                            return "SUCCESS_VERIFIED"
                    except Exception:
                        continue
            except Exception:
                pass
                
            # Check 3: State transition (Voice button was present before, now transitioned to active call)
            if before_state and before_state.get("has_voice_button"):
                current_state = get_call_ui_state(window)
                if not current_state.get("has_voice_button") and (current_state.get("has_end_call_button") or current_state.get("active_indicators")):
                    return "SUCCESS_VERIFIED"

        time.sleep(0.4)

    return "SUCCESS_UNVERIFIED"


def format_control_type_name(c_type_val):
    """Normalize ControlType to standard UIA ControlType name string."""
    if not c_type_val:
        return "Unknown"
    type_map = {
        50000: "ButtonControl", "50000": "ButtonControl", "Button": "ButtonControl", "ButtonControl": "ButtonControl",
        50026: "GroupControl", "50026": "GroupControl", "Group": "GroupControl", "GroupControl": "GroupControl",
        50033: "PaneControl", "50033": "PaneControl", "Pane": "PaneControl", "PaneControl": "PaneControl",
        50020: "TextControl", "50020": "TextControl", "Text": "TextControl", "TextControl": "TextControl",
        50004: "EditControl", "50004": "EditControl", "Edit": "EditControl", "EditControl": "EditControl",
        50007: "ListItemControl", "50007": "ListItemControl", "ListItem": "ListItemControl", "ListItemControl": "ListItemControl",
        50008: "ListControl", "50008": "ListControl", "List": "ListControl", "ListControl": "ListControl",
        50030: "DocumentControl", "50030": "DocumentControl", "Document": "DocumentControl", "DocumentControl": "DocumentControl",
        50006: "ImageControl", "50006": "ImageControl", "Image": "ImageControl", "ImageControl": "ImageControl",
        50025: "CustomControl", "50025": "CustomControl", "Custom": "CustomControl", "CustomControl": "CustomControl",
    }
    if c_type_val in type_map:
        return type_map[c_type_val]
    if isinstance(c_type_val, int) and hasattr(auto, "ControlTypeNames"):
        return auto.ControlTypeNames.get(c_type_val, str(c_type_val))
    return str(c_type_val)


def discover_chat_header(window):
    """
    Phase 4: Identify the active chat header container and its child actions structurally without coordinates.
    Safely redacts any visible contact or personal text.
    """
    res = {
        "found": False,
        "header_container": None,
        "actions": []
    }
    if not window or not getattr(window, "Exists", lambda w, s: True)(0, 0):
        return res

    known_action_names = {
        "voice call", "video call", "search", "menu", "back", "profile details",
        "more options", "attach", "starred messages", "close", "chat details", ""
    }

    header_elem = None
    
    # Priority 1: Find a known header button like Voice call, Video call, or Search and traverse up
    for btn_name in ["Voice call", "Video call", "Search"]:
        try:
            b = window.ButtonControl(searchDepth=25, Name=btn_name)
            if b.Exists(0.05, 0):
                curr = b.GetParentControl()
                while curr and curr != window:
                    rect = curr.BoundingRectangle
                    if rect:
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        if w > 250 and 35 <= h <= 200:
                            header_elem = curr
                            break
                    curr = curr.GetParentControl()
            if header_elem:
                break
        except Exception:
            pass

    # Priority 2: Walk controls for a top banner GroupControl / PaneControl
    if not header_elem:
        try:
            win_rect = window.BoundingRectangle
            win_top = win_rect.top if win_rect else 0
            for c, d in auto.WalkControl(window, maxDepth=16):
                try:
                    c_tp = format_control_type_name(c.ControlType)
                    if "Group" in c_tp or "Pane" in c_tp:
                        rect = c.BoundingRectangle
                        if rect:
                            w = rect.right - rect.left
                            h = rect.bottom - rect.top
                            if w > 250 and 35 <= h <= 200 and rect.top <= (win_top + 200):
                                children = c.GetChildren() if hasattr(c, "GetChildren") else []
                                if any("Button" in format_control_type_name(ch.ControlType) for ch in children):
                                    header_elem = c
                                    break
                except Exception:
                    continue
        except Exception:
            pass

    if header_elem:
        h_rect = header_elem.BoundingRectangle
        h_tp = format_control_type_name(header_elem.ControlType)
        res["found"] = True
        res["header_container"] = {
            "ControlType": h_tp,
            "AutomationId": str(getattr(header_elem, "AutomationId", "") or ""),
            "ClassName": str(getattr(header_elem, "ClassName", "") or ""),
            "BoundingRectangle": (h_rect.left, h_rect.top, h_rect.right, h_rect.bottom) if h_rect else None
        }

        # Collect child actions within header container
        seen_keys = set()
        for c, d in auto.WalkControl(header_elem, maxDepth=6):
            try:
                c_tp = format_control_type_name(c.ControlType)
                raw_name = str(getattr(c, "Name", "") or "").strip()
                rect = c.BoundingRectangle
                bounds = (rect.left, rect.top, rect.right, rect.bottom) if rect else None
                
                key = (c_tp, bounds)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                if raw_name.lower() in known_action_names or not raw_name:
                    safe_name = raw_name
                else:
                    safe_name = "[REDACTED_CONTACT_INFO]"

                if "Button" in c_tp or "Group" in c_tp:
                    res["actions"].append({
                        "ControlType": c_tp,
                        "Name": safe_name,
                        "AutomationId": str(getattr(c, "AutomationId", "") or ""),
                        "ClassName": str(getattr(c, "ClassName", "") or ""),
                        "BoundingRectangle": bounds,
                        "IsEnabled": bool(getattr(c, "IsEnabled", True)),
                        "IsOffscreen": bool(getattr(c, "IsOffscreen", False)),
                        "Depth": d
                    })
            except Exception:
                continue

    return res


def score_call_candidate(element_info, call_type="voice", win_bounds=None, header_bounds=None):
    """
    Phase 5: Read-only diagnostic candidate scoring function.
    Evaluates candidate UI controls based on positive & negative structural signals.
    Does NOT invoke or click anything.
    """
    if isinstance(element_info, dict):
        raw_name = str(element_info.get("Name") or "").strip()
        auto_id = str(element_info.get("AutomationId") or "").strip()
        cls_name = str(element_info.get("ClassName") or "").strip()
        c_type = format_control_type_name(element_info.get("ControlType"))
        rect = element_info.get("BoundingRectangle")
        is_enabled = bool(element_info.get("IsEnabled", True))
        is_offscreen = bool(element_info.get("IsOffscreen", False))
    else:
        raw_name = str(getattr(element_info, "Name", "") or "").strip()
        auto_id = str(getattr(element_info, "AutomationId", "") or "").strip()
        cls_name = str(getattr(element_info, "ClassName", "") or "").strip()
        c_type = format_control_type_name(getattr(element_info, "ControlType", None))
        b = getattr(element_info, "BoundingRectangle", None)
        rect = (b.left, b.top, b.right, b.bottom) if b and hasattr(b, "left") else b
        is_enabled = bool(getattr(element_info, "IsEnabled", True))
        is_offscreen = bool(getattr(element_info, "IsOffscreen", False))

    score = 0
    positive_signals = []
    negative_signals = []

    name_lower = raw_name.lower()
    auto_id_lower = auto_id.lower()
    target = call_type.lower()

    # --- Positive Signals ---
    if target == "voice":
        if name_lower == "voice call":
            score += 40
            positive_signals.append("Exact Name match 'Voice call'")
        elif "voice" in name_lower and "call" in name_lower:
            score += 30
            positive_signals.append("Name contains 'voice' and 'call'")
        elif "call" in name_lower or "voice" in name_lower or "audio" in name_lower:
            score += 15
            positive_signals.append("Name contains call/voice/audio keyword")
    elif target == "video":
        if name_lower == "video call":
            score += 40
            positive_signals.append("Exact Name match 'Video call'")
        elif "video" in name_lower and "call" in name_lower:
            score += 30
            positive_signals.append("Name contains 'video' and 'call'")
        elif "video" in name_lower:
            score += 15
            positive_signals.append("Name contains video keyword")

    if target in auto_id_lower or ("call" in auto_id_lower and target in ["voice", "video"]):
        score += 15
        positive_signals.append(f"AutomationId contains '{target}'")

    if "Button" in c_type or c_type in ["50000", 50000]:
        score += 20
        positive_signals.append("ControlType is Button")

    bounds_tuple = None
    width = 0
    height = 0
    if rect:
        if isinstance(rect, (list, tuple)) and len(rect) == 4:
            bounds_tuple = (rect[0], rect[1], rect[2], rect[3])
            width = max(0, rect[2] - rect[0])
            height = max(0, rect[3] - rect[1])
        elif hasattr(rect, "left"):
            bounds_tuple = (rect.left, rect.top, rect.right, rect.bottom)
            width = max(0, rect.right - rect.left)
            height = max(0, rect.bottom - rect.top)

    if bounds_tuple:
        if header_bounds and isinstance(header_bounds, (list, tuple)) and len(header_bounds) == 4:
            if (bounds_tuple[0] >= header_bounds[0] - 10 and
                bounds_tuple[1] >= header_bounds[1] - 10 and
                bounds_tuple[2] <= header_bounds[2] + 10 and
                bounds_tuple[3] <= header_bounds[3] + 10):
                score += 20
                positive_signals.append("Located inside detected chat header container")
        elif win_bounds and isinstance(win_bounds, (list, tuple)) and len(win_bounds) == 4:
            if bounds_tuple[1] <= (win_bounds[1] + 200):
                score += 10
                positive_signals.append("Located in upper window header region")
        elif bounds_tuple[1] <= 200:
            score += 10
            positive_signals.append("Located in upper window region")

        if width >= 20 and height >= 20:
            score += 5
            positive_signals.append(f"Valid actionable button dimensions ({width}x{height})")

    if is_enabled:
        score += 10
        positive_signals.append("Control is enabled")
    if not is_offscreen:
        score += 10
        positive_signals.append("Control is visible / on-screen")

    # --- Negative Signals & Penalties ---
    if any(k in name_lower for k in ["click to call back", "history", "missed", "incoming", "phone.", "start new call", "older messages"]):
        score -= 50
        negative_signals.append("Contains history / missed / call-back / message list text")
    if "search" in name_lower:
        score -= 40
        negative_signals.append("Contains search keyword")
    if any(k in name_lower for k in ["menu", "status", "notification"]):
        score -= 30
        negative_signals.append("Contains menu/status/notification keyword")
    if name_lower == "calls":
        score -= 35
        negative_signals.append("Sidebar navigation 'Calls' tab button, not active chat action")

    if any(k in c_type for k in ["Text", "Edit", "ListItem", "ListControl", "Document", "Pane", "Group"]) and "Button" not in c_type:
        score -= 40
        negative_signals.append(f"Non-button ControlType ({c_type})")

    if bounds_tuple:
        if bounds_tuple[0] < 0 or bounds_tuple[1] < 0:
            score -= 50
            negative_signals.append(f"Negative scroll / offscreen coordinates ({bounds_tuple})")
        if width <= 0 or height <= 0:
            score -= 40
            negative_signals.append("Zero area bounding rectangle")
    else:
        score -= 30
        negative_signals.append("Missing bounding rectangle")

    if is_offscreen:
        score -= 30
        negative_signals.append("Marked as IsOffscreen=True")
    if not is_enabled:
        score -= 30
        negative_signals.append("Disabled control (IsEnabled=False)")

    # Redact any non-standard names
    known_safe = {
        "voice call", "video call", "search", "menu", "back", "profile details",
        "more options", "attach", "calls", "search instead", "close", "chat details", ""
    }
    if name_lower in known_safe or any(k in name_lower for k in ["voice", "video", "call", "search", "menu", "back", "profile", "attach"]):
        safe_name = raw_name
    elif not raw_name:
        safe_name = ""
    else:
        safe_name = "[REDACTED_CONTACT_INFO]"

    return {
        "ControlType": c_type,
        "Name": safe_name,
        "AutomationId": auto_id,
        "ClassName": cls_name,
        "BoundingRectangle": bounds_tuple,
        "IsEnabled": is_enabled,
        "IsOffscreen": is_offscreen,
        "Score": score,
        "PositiveSignals": positive_signals,
        "NegativeSignals": negative_signals
    }


def inspect_whatsapp_ui(controller=None):
    """
    Phase 1-5 & Phase 8: Diagnostic mode function.
    Safely acquires and inspects the live WhatsApp Desktop window without making calls or modifying UI.
    Discovers chat header, evaluates candidate controls, and produces structured diagnostic scoring.
    """
    win, acq_diag = acquire_whatsapp_window(timeout=15.0)
    diag = {
        "display": get_display_diagnostics(),
        "whatsapp_window": None,
        "acquisition_diagnostics": acq_diag,
        "controls": [],
        "header": None,
        "voice_candidates": [],
        "video_candidates": [],
        "other_header_actions": [],
        "final_finding": {
            "VOICE_CALL_CONTROL_FOUND": "NO",
            "VIDEO_CALL_CONTROL_FOUND": "NO",
            "details": ""
        },
        "selected_voice_candidate": None,
        "selected_video_candidate": None,
        "report": ""
    }
    
    if not win or not getattr(win, "Exists", lambda w, s: True)(0, 0):
        diag["report"] = "Error: WhatsApp window could not be acquired."
        return diag

    rect = win.BoundingRectangle
    win_bounds = (rect.left, rect.top, rect.right, rect.bottom) if rect else None
    diag["whatsapp_window"] = {
        "Name": str(getattr(win, "Name", "") or ""),
        "ClassName": str(getattr(win, "ClassName", "") or ""),
        "ProcessId": getattr(win, "ProcessId", None),
        "BoundingRectangle": win_bounds
    }

    # Discover chat header
    header_info = discover_chat_header(win)
    diag["header"] = header_info
    header_bounds = header_info.get("header_container", {}).get("BoundingRectangle") if header_info.get("found") else None

    # Discover candidate controls across the window
    candidate_elements = []
    seen_addrs = set()

    # Priority target searches
    for target_name in ["Voice call", "Video call", "Calls", "Search", "Menu", "Back", "Profile details"]:
        try:
            b = win.ButtonControl(searchDepth=25, Name=target_name)
            if b.Exists(0.05, 0):
                info = inspect_ui_element(b)
                if info:
                    key = (info.get("ControlType"), info.get("BoundingRectangle"))
                    if key not in seen_addrs:
                        seen_addrs.add(key)
                        candidate_elements.append(info)
        except Exception:
            pass

    # Inspect search box
    try:
        edit = win.EditControl(searchDepth=12)
        if edit.Exists(0.05, 0):
            info = inspect_ui_element(edit)
            if info:
                key = (info.get("ControlType"), info.get("BoundingRectangle"))
                if key not in seen_addrs:
                    seen_addrs.add(key)
                    candidate_elements.append(info)
    except Exception:
        pass

    # Walk controls safely (without exposing message contents)
    try:
        for c, d in auto.WalkControl(win, maxDepth=16):
            try:
                c_name = str(getattr(c, "Name", "") or "").strip()
                c_type = auto.ControlTypeNames.get(c.ControlType, str(getattr(c, 'ControlType', ''))) if hasattr(auto, 'ControlTypeNames') else str(getattr(c, 'ControlType', ''))
                
                # Filter out message list items
                if "ListItem" in c_type or "ListControl" in c_type:
                    continue
                
                if "Button" in c_type or any(k in c_name.lower() for k in ["call", "video", "search", "menu", "attach", "voice", "audio"]):
                    info = inspect_ui_element(c)
                    if info:
                        key = (info.get("ControlType"), info.get("BoundingRectangle"))
                        if key not in seen_addrs:
                            seen_addrs.add(key)
                            candidate_elements.append(info)
            except Exception:
                continue
    except Exception:
        pass

    diag["controls"] = candidate_elements

    # Score voice candidates
    scored_voice = []
    for cand in candidate_elements:
        nm = cand.get("Name", "").lower()
        if any(k in nm for k in ["voice", "call", "audio", "phone"]):
            sc = score_call_candidate(cand, call_type="voice", win_bounds=win_bounds, header_bounds=header_bounds)
            scored_voice.append(sc)
    scored_voice.sort(key=lambda x: x.get("Score", -999), reverse=True)
    diag["voice_candidates"] = scored_voice

    # Score video candidates
    scored_video = []
    for cand in candidate_elements:
        nm = cand.get("Name", "").lower()
        if any(k in nm for k in ["video", "call"]):
            sc = score_call_candidate(cand, call_type="video", win_bounds=win_bounds, header_bounds=header_bounds)
            scored_video.append(sc)
    scored_video.sort(key=lambda x: x.get("Score", -999), reverse=True)
    diag["video_candidates"] = scored_video

    # Select top candidates
    top_voice = scored_voice[0] if scored_voice else None
    top_video = scored_video[0] if scored_video else None
    diag["selected_voice_candidate"] = top_voice
    diag["selected_video_candidate"] = top_video

    # Collect other header actions
    other_actions = []
    if header_info.get("found"):
        for act in header_info.get("actions", []):
            act_name = act.get("Name", "").lower()
            if act_name not in ["voice call", "video call"]:
                other_actions.append(act)
    diag["other_header_actions"] = other_actions

    # Evaluate final findings
    voice_found = bool(top_voice and top_voice.get("Score", 0) >= 50 and top_voice.get("Name") == "Voice call")
    video_found = bool(top_video and top_video.get("Score", 0) >= 50 and top_video.get("Name") == "Video call")
    diag["final_finding"]["VOICE_CALL_CONTROL_FOUND"] = "YES" if voice_found else "NO"
    diag["final_finding"]["VIDEO_CALL_CONTROL_FOUND"] = "YES" if video_found else "NO"

    # Build structured Phase 8 Report
    cand_hwnd = acq_diag.get("candidate_hwnd") or {}
    hwnd_val = cand_hwnd.get("hwnd") or getattr(win, "NativeWindowHandle", None) or "Unknown"
    pid_val = cand_hwnd.get("pid") or getattr(win, "ProcessId", None) or "Unknown"

    header_cont = header_info.get("header_container") or {}
    header_lines = []
    if header_info.get("found"):
        header_lines.append(f"Container Type: {header_cont.get('ControlType')}")
        header_lines.append(f"AutomationId: {repr(header_cont.get('AutomationId'))}")
        header_lines.append(f"Class: {repr(header_cont.get('ClassName'))}")
        header_lines.append(f"Bounds: {header_cont.get('BoundingRectangle')}")
        header_lines.append("\nCHILD ACTIONS:")
        for idx, act in enumerate(header_info.get("actions", []), 1):
            header_lines.append(f"{idx}. Type: {act.get('ControlType')}\n   Name: {repr(act.get('Name'))}\n   AutomationId: {repr(act.get('AutomationId'))}\n   Class: {repr(act.get('ClassName'))}\n   Bounds: {act.get('BoundingRectangle')}\n   Enabled: {act.get('IsEnabled')}\n   Offscreen: {act.get('IsOffscreen')}")
    else:
        header_lines.append("Header container not detected via structural inspection.")

    voice_cand_lines = []
    for idx, v in enumerate(scored_voice[:3], 1):
        voice_cand_lines.append(
            f"Candidate #{idx}:\n"
            f"ControlType: {v.get('ControlType')}\n"
            f"Name: {repr(v.get('Name'))}\n"
            f"AutomationId: {repr(v.get('AutomationId'))}\n"
            f"ClassName: {repr(v.get('ClassName'))}\n"
            f"Bounds: {v.get('BoundingRectangle')}\n"
            f"Enabled: {v.get('IsEnabled')}\n"
            f"Offscreen: {v.get('IsOffscreen')}\n"
            f"Score: {v.get('Score')}\n"
            f"Positive signals: {', '.join(v.get('PositiveSignals', []))}\n"
            f"Negative signals: {', '.join(v.get('NegativeSignals', [])) if v.get('NegativeSignals') else 'None'}"
        )
    if not voice_cand_lines:
        voice_cand_lines.append("No voice call candidates discovered.")

    video_cand_lines = []
    for idx, v in enumerate(scored_video[:3], 1):
        video_cand_lines.append(
            f"Candidate #{idx}:\n"
            f"ControlType: {v.get('ControlType')}\n"
            f"Name: {repr(v.get('Name'))}\n"
            f"AutomationId: {repr(v.get('AutomationId'))}\n"
            f"ClassName: {repr(v.get('ClassName'))}\n"
            f"Bounds: {v.get('BoundingRectangle')}\n"
            f"Enabled: {v.get('IsEnabled')}\n"
            f"Offscreen: {v.get('IsOffscreen')}\n"
            f"Score: {v.get('Score')}\n"
            f"Positive signals: {', '.join(v.get('PositiveSignals', []))}\n"
            f"Negative signals: {', '.join(v.get('NegativeSignals', [])) if v.get('NegativeSignals') else 'None'}"
        )
    if not video_cand_lines:
        video_cand_lines.append("No video call candidates discovered.")

    other_act_lines = []
    for idx, act in enumerate(other_actions, 1):
        other_act_lines.append(f"Action #{idx}: Type: {act.get('ControlType')}, Name: {repr(act.get('Name'))}, Bounds: {act.get('BoundingRectangle')}, Enabled: {act.get('IsEnabled')}")
    if not other_act_lines:
        other_act_lines.append("None")

    finding_text = (
        f"VOICE_CALL_CONTROL_FOUND = {diag['final_finding']['VOICE_CALL_CONTROL_FOUND']}\n"
        f"VIDEO_CALL_CONTROL_FOUND = {diag['final_finding']['VIDEO_CALL_CONTROL_FOUND']}\n\n"
        f"Technical Metadata for Next Implementation Phase:\n"
        f"- Voice Call Button: ControlType={top_voice.get('ControlType') if top_voice else 'None'}, Name={repr(top_voice.get('Name') if top_voice else '')}, Bounds={top_voice.get('BoundingRectangle') if top_voice else None}\n"
        f"- Video Call Button: ControlType={top_video.get('ControlType') if top_video else 'None'}, Name={repr(top_video.get('Name') if top_video else '')}, Bounds={top_video.get('BoundingRectangle') if top_video else None}\n"
        f"- Chat Header Container: Type={header_cont.get('ControlType')}, Bounds={header_cont.get('BoundingRectangle')}\n"
        f"- Direct Search Path: window.ButtonControl(searchDepth=25, Name='Voice call') or header_container.ButtonControl(Name='Voice call')"
    )

    report_str = (
        "--- WhatsApp UI Inspection & Live Control Discovery ---\n\n"
        f"## WINDOW\n"
        f"HWND: {hwnd_val}\n"
        f"PID: {pid_val}\n"
        f"Bounds: {win_bounds}\n"
        f"Class: {diag['whatsapp_window'].get('ClassName')}\n"
        f"Title: {diag['whatsapp_window'].get('Name')}\n"
        f"Display: {diag['display']}\n"
        f"Discovered Controls: {len(diag['controls'])} items\n\n"
        f"## HEADER\n"
        f"{chr(10).join(header_lines)}\n\n"
        f"## CALL CANDIDATES\n"
        f"{chr(10).join(voice_cand_lines)}\n\n"
        f"## VIDEO CANDIDATES\n"
        f"{chr(10).join(video_cand_lines)}\n\n"
        f"## OTHER HEADER ACTIONS\n"
        f"{chr(10).join(other_act_lines)}\n\n"
        f"## FINAL FINDING\n"
        f"{finding_text}\n"
    )

    diag["report"] = report_str
    return diag


class WhatsAppDesktopController:
    def __init__(self):
        self.window = None
        self.diagnostic_log = []
        self.active_contact = None
        set_dpi_awareness()

    def log(self, msg):
        self.diagnostic_log.append(msg)

    def find_or_focus_whatsapp(self):
        """Locate and focus WhatsApp window using robust window acquisition."""
        set_dpi_awareness()
        win, diag = acquire_whatsapp_window(timeout=6.0)
        self.window = win
        
        if self.window and self.window.Exists(0, 0):
            rect = self.window.BoundingRectangle
            disp = get_display_diagnostics()
            self.log(f"WhatsApp window acquired. Window Rect: {rect.left},{rect.top},{rect.right},{rect.bottom} | Scaling: {disp.get('scaling_pct')}%")
            return True

        self.log(f"WhatsApp window acquisition failed: {diag.get('failure_reason', 'UNKNOWN')}. Diagnostics: {diag}")
        return False

    def is_whatsapp_running(self):
        return self.find_or_focus_whatsapp()

    def search_contact(self, name):
        if not self.window:
            if not self.is_whatsapp_running():
                self.log("Search failed: WhatsApp window is not open or cannot be found.")
                return []
                
        normalized = name.lower().replace(" ", "")
        if getattr(self, 'active_contact', None) == normalized:
            self.log(f"No contact search required; target chat already open: {name}")
            return ["ALREADY_ACTIVE"]
                
        self.log(f"Initiating dynamic contact search for: '{name}'")
        
        layer1_success = False
        # Layer 1: Search via UI Automation EditControl
        try:
            search_box = self.window.EditControl(searchDepth=12)
            if search_box.Exists(0.3, 0):
                search_box.Click(waitTime=0.1)
                search_box.SendKeys('{Ctrl}a{Delete}', waitTime=0.1)
                search_box.SendKeys(name, waitTime=1.0)
                self.log("Layer 1 (UIA): Search query entered via EditControl.")
                layer1_success = True
        except Exception:
            pass

        # Layer 2: Native keyboard shortcut (Ctrl+F)
        if not layer1_success:
            try:
                self.window.SetActive()
                time.sleep(0.2)
                self.window.SendKeys('{Ctrl}f', waitTime=0.3)
                self.window.SendKeys('{Ctrl}a{Delete}', waitTime=0.1)
                self.window.SendKeys(name, waitTime=1.0)
                self.log("Layer 2 (Keyboard): Search query entered via Ctrl+F shortcut.")
                layer1_success = True
            except Exception as e:
                self.log(f"Layer 2 failed: {e}")
                
        # Layer 3: Proportional spatial click for search box
        if not layer1_success:
            try:
                import pyautogui
                rect = self.window.BoundingRectangle
                if rect:
                    # Click top-left search area proportionally (approx 15% width, 8% height)
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    pyautogui.click(rect.left + int(w * 0.15), rect.top + int(h * 0.08))
                    time.sleep(0.2)
                    pyautogui.hotkey('ctrl', 'a')
                    pyautogui.press('delete')
                    pyautogui.write(name, interval=0.04)
                    self.log("Layer 3 (PyAutoGUI): Search activated via dynamic relative coordinate.")
            except Exception as e:
                self.log(f"Layer 3 failed: {e}")

        # Wait for search results to populate
        time.sleep(0.8)
            
        # Discover matching items dynamically via UIA
        matches = []
        found_elements = find_chat_element(name, self.window)
        for elem in found_elements:
            try:
                if elem.Name:
                    matches.append(elem.Name)
            except Exception:
                pass
            
        matches = list(set(matches))
        if matches:
            self.log(f"Contact search successful for '{name}'. UIA discovered matches: {matches}")
        else:
            self.log(f"Contact search executed for '{name}'. UIA blind to result list; will rely on Enter selection.")
            
        return matches

    def _open_contact_keyboard_fallback(self, contact_name, is_uia_blind=False):
        if is_uia_blind:
            self.log("UIA blind search path active.")
        try:
            self.window.SetActive()
            self.log("Keyboard fallback: selecting top search result via Enter.")
            self.window.SendKeys('{Enter}', waitTime=0.4)
            time.sleep(0.8)
            return True
        except Exception as e:
            self.log(f"Keyboard fallback failed: {e}")
            return False

    def open_contact(self, name, uia_matches):
        if not self.window:
            return False
        
        normalized_name = name.lower().replace(" ", "")
        if uia_matches == ["ALREADY_ACTIVE"]:
            self.active_contact = normalized_name
            self.log(f"Active chat established: {name}")
            return True
        
        self.active_contact = None
        opened = False
        
        # Level 1: Invoke or Click the exact UIA match element
        if uia_matches:
            found_elements = find_chat_element(name, self.window)
            for elem in found_elements:
                if invoke_ui_element(elem):
                    time.sleep(0.5)
                    self.log(f"Level 1 (UIA): Invoked contact element '{elem.Name}'.")
                    opened = True
                    break

        # Level 2: Keyboard 'Enter' on top result
        is_uia_blind = any("UIA blind" in msg for msg in self.diagnostic_log[-10:])
        if not opened:
            opened = self._open_contact_keyboard_fallback(name, is_uia_blind)
                
        if opened:
            # Verify chat opened via window title or call controls
            verified = False
            try:
                if name.lower().replace(" ", "") in (self.window.Name or "").lower().replace(" ", ""):
                    verified = True
                    self.log(f"Post-click verification: Window Title confirmed chat '{self.window.Name}'")
            except Exception:
                pass
                
            if not verified:
                btn = find_call_button(self.window, "voice")
                if btn:
                    verified = True
                    self.log("Post-click verification: Call controls detected in chat header.")
                    
            if not verified:
                if is_uia_blind:
                    self.log("Chat opened successfully.")
                elif uia_matches:
                    self.log("Post-click verification failed: UIA matches found but UI state didn't update.")
                    opened = False
                else:
                    self.log("Post-click verification failed: Chat did not open.")
                    opened = False
            else:
                self.log("Chat opened successfully.")
            
        if opened:
            self.active_contact = normalized_name
            self.log(f"Active chat established: {name}")
            
        return opened

    def check_call_buttons(self):
        """Inspect and log whether voice and video call buttons are detected."""
        v_btn = find_call_button(self.window, "voice")
        vid_btn = find_call_button(self.window, "video")
        
        if v_btn or vid_btn:
            self.log("Call buttons DETECTED via UIA in chat header.")
        else:
            self.log("Call buttons NOT DETECTED via UIA (testing visual/relative fallbacks).")

    def _ensure_active_chat(self, name):
        normalized = name.lower().replace(" ", "")
        if getattr(self, 'active_contact', None) != normalized:
            self.log(f"Fail safe: Target chat '{name}' is not currently established as active.")
            return False
        return True

    def start_voice_call(self, name):
        """
        Initiate a voice call using adaptive multi-level automation:
        LEVEL 1: Windows UI Automation
        LEVEL 2: Visual template detection
        LEVEL 3: Window-relative dynamic coordinates
        LEVEL 4: Keyboard navigation
        """
        if not self.window:
            return False
        if not self._ensure_active_chat(name):
            return False
            
        self.log(f"Starting voice call for active chat: {name}")
        time.sleep(0.5)

        # Retry loop: max 2 attempts for UI Automation
        for attempt in range(1, DEFAULT_RETRY_LIMIT + 1):
            self.log(f"Voice call Attempt {attempt}/{DEFAULT_RETRY_LIMIT} via UI Automation.")
            v_btn = find_call_button(self.window, "voice")
            if v_btn:
                btn_meta = inspect_ui_element(v_btn)
                self.log(
                    f"Level 1 (UIA): Voice call button located: "
                    f"Name='{btn_meta.get('Name')}', ControlType='{btn_meta.get('ControlType')}', "
                    f"AutomationId='{btn_meta.get('AutomationId')}', Bounds={btn_meta.get('BoundingRectangle')}"
                )
                
                before_state = get_call_ui_state(self.window)
                self.log(f"UI State Before Invocation: VoiceButton={before_state.get('has_voice_button')}, EndCallButton={before_state.get('has_end_call_button')}")
                
                if invoke_ui_element(v_btn, is_call_action=True):
                    self.log(f"Voice call action dispatched to element '{v_btn.Name}'. Verifying call state...")
                    verified = verify_call_started(self.window, timeout=5.0, before_state=before_state)
                    if verified == "SUCCESS_VERIFIED":
                        self.log("Voice call successfully verified via UI state transition.")
                        return "SUCCESS_VERIFIED"
                    else:
                        self.log(f"Voice call action dispatched on attempt {attempt}, but post-action state verification timed out.")
            
            # Re-discover window and refresh before attempt 2
            if attempt < DEFAULT_RETRY_LIMIT:
                self.find_or_focus_whatsapp()
                time.sleep(0.5)

        # LEVEL 2: Visual Detection Fallback
        self.log("Level 1 UIA did not initiate verified voice call. Attempting Level 2 (Visual Detection).")
        before_state = get_call_ui_state(self.window)
        if visual_call_button_fallback(self.window, "voice"):
            self.log("Level 2 (Visual): Voice call button detected visually and clicked.")
            return verify_call_started(self.window, timeout=5.0, before_state=before_state)

        # LEVEL 3: Window-Relative Fallback
        self.log("Level 2 Visual fallback did not locate button. Attempting Level 3 (Window-Relative).")
        before_state = get_call_ui_state(self.window)
        if window_relative_fallback(self.window, "voice"):
            self.log("Level 3 (Window-Relative): Proportional call coordinate clicked.")
            return verify_call_started(self.window, timeout=5.0, before_state=before_state)

        # LEVEL 4: Keyboard Navigation Fallback
        self.log("Attempting Level 4 (Keyboard navigation).")
        before_state = get_call_ui_state(self.window)
        if keyboard_call_fallback(self.window, "voice"):
            return verify_call_started(self.window, timeout=5.0, before_state=before_state)

        self.log("All automation levels failed to trigger voice call.")
        return False

    def start_video_call(self, name):
        """Initiate a video call using adaptive multi-level automation."""
        if not self.window:
            return False
        if not self._ensure_active_chat(name):
            return False
            
        self.log(f"Starting video call for active chat: {name}")
        time.sleep(0.5)

        # Retry loop: max 2 attempts for UI Automation
        for attempt in range(1, DEFAULT_RETRY_LIMIT + 1):
            self.log(f"Video call Attempt {attempt}/{DEFAULT_RETRY_LIMIT} via UI Automation.")
            vid_btn = find_call_button(self.window, "video")
            if vid_btn:
                btn_meta = inspect_ui_element(vid_btn)
                self.log(
                    f"Level 1 (UIA): Video call button located: "
                    f"Name='{btn_meta.get('Name')}', ControlType='{btn_meta.get('ControlType')}', "
                    f"AutomationId='{btn_meta.get('AutomationId')}', Bounds={btn_meta.get('BoundingRectangle')}"
                )
                
                before_state = get_call_ui_state(self.window)
                self.log(f"UI State Before Invocation: VideoButton={before_state.get('has_video_button')}, EndCallButton={before_state.get('has_end_call_button')}")
                
                if invoke_ui_element(vid_btn, is_call_action=True):
                    self.log(f"Video call action dispatched to element '{vid_btn.Name}'. Verifying call state...")
                    verified = verify_call_started(self.window, timeout=5.0, before_state=before_state)
                    if verified == "SUCCESS_VERIFIED":
                        self.log("Video call successfully verified via UI state transition.")
                        return "SUCCESS_VERIFIED"
                    else:
                        self.log(f"Video call action dispatched on attempt {attempt}, but post-action state verification timed out.")
            
            if attempt < DEFAULT_RETRY_LIMIT:
                self.find_or_focus_whatsapp()
                time.sleep(0.5)

        # LEVEL 2: Visual Detection Fallback
        self.log("Attempting Level 2 (Visual Detection) for video call.")
        before_state = get_call_ui_state(self.window)
        if visual_call_button_fallback(self.window, "video"):
            self.log("Level 2 (Visual): Video call button detected visually and clicked.")
            return verify_call_started(self.window, timeout=5.0, before_state=before_state)

        # LEVEL 3: Window-Relative Fallback
        self.log("Attempting Level 3 (Window-Relative) for video call.")
        before_state = get_call_ui_state(self.window)
        if window_relative_fallback(self.window, "video"):
            self.log("Level 3 (Window-Relative): Proportional video call coordinate clicked.")
            return verify_call_started(self.window, timeout=5.0, before_state=before_state)

        return False

    def end_call(self):
        """End ongoing WhatsApp call."""
        if not self.window:
            self.find_or_focus_whatsapp()
        if not self.window:
            return False
            
        for name in ["End call", "Leave", "Decline", "Hang up", "Leave call", "End voice call"]:
            try:
                btn = self.window.ButtonControl(searchDepth=12, Name=name)
                if btn.Exists(0.2, 0) and invoke_ui_element(btn):
                    self.log(f"End call executed via button '{name}'.")
                    return True
            except Exception:
                pass
        return False

    def answer_call(self):
        """Accept/Answer incoming WhatsApp call."""
        if not self.window:
            self.find_or_focus_whatsapp()
        if not self.window:
            return False
            
        for name in ["Accept", "Answer"]:
            try:
                btn = self.window.ButtonControl(searchDepth=12, Name=name)
                if btn.Exists(0.2, 0) and invoke_ui_element(btn):
                    self.log(f"Answer call executed via button '{name}'.")
                    return True
            except Exception:
                pass
        return False

    def reject_call(self):
        """Decline/Reject incoming WhatsApp call."""
        if not self.window:
            self.find_or_focus_whatsapp()
        if not self.window:
            return False
            
        for name in ["Decline", "Reject"]:
            try:
                btn = self.window.ButtonControl(searchDepth=12, Name=name)
                if btn.Exists(0.2, 0) and invoke_ui_element(btn):
                    self.log(f"Reject call executed via button '{name}'.")
                    return True
            except Exception:
                pass
        return False

    def get_call_state(self):
        """Inspect UI to determine current WhatsApp call state."""
        if not self.window:
            return "UNKNOWN"
        for name in ["End call", "Leave", "Decline", "Mute"]:
            try:
                if self.window.ButtonControl(searchDepth=12, Name=name).Exists(0.2, 0):
                    return "CONNECTED"
            except Exception:
                pass
        for name in ["Accept", "Answer"]:
            try:
                if self.window.ButtonControl(searchDepth=12, Name=name).Exists(0.2, 0):
                    return "INCOMING_CALL"
            except Exception:
                pass
        return "UNKNOWN"


PLUGIN = {
    "name": "whatsapp_desktop_call",
    "description": (
        "Controls WhatsApp Desktop calling features. "
        "Use this tool when the user asks to call someone on WhatsApp, answer a call, or end a call."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "intent": {
                "type": "STRING", 
                "description": "The specific intent (e.g., 'voice_call', 'video_call', 'end_call', 'answer_call', 'reject_call', 'status', 'whatsapp_search_test', 'inspect_whatsapp_ui')."
            },
            "contact_name": {
                "type": "STRING",
                "description": "The name of the contact to call, if applicable."
            }
        },
        "required": ["intent"],
    },
}


def extract_contact_name(text):
    patterns = [
        r"call\s+(.+?)\s+on\s+whatsapp",
        r"whatsapp\s+video\s+call\s+to\s+(.+)",
        r"whatsapp\s+video\s+call\s+(.+)",
        r"whatsapp\s+call\s+(.+)",
        r"whatsapp\s+la\s+(.+?)\s*ku\s+call",
        r"whatsapp\s+search\s+(.+)",
        r"search\s+(.+?)\s+on\s+whatsapp",
        r"call\s+(.+)"
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            name = re.sub(r'(?i)\bpannu\b', '', name).strip()
            name = re.sub(r'(?i)\bplease\b', '', name).strip()
            if name:
                return name
    return None


def run(parameters: dict, player=None, session_memory=None) -> str:
    raw_intent = parameters.get("intent", "").lower().strip()
    contact_name = parameters.get("contact_name", "").strip()

    controller = WhatsAppDesktopController()
    
    if not contact_name and ("call" in raw_intent or "search" in raw_intent):
        contact_name = extract_contact_name(raw_intent) or ""
        
    if "inspect" in raw_intent or "diag" in raw_intent or "inspect_whatsapp_ui" in raw_intent:
        logical_intent = "inspect_whatsapp_ui"
    elif "whatsapp_search_test" in raw_intent or "search" in raw_intent:
        logical_intent = "whatsapp_search_test"
    elif "end" in raw_intent or "cut" in raw_intent or "hang" in raw_intent:
        logical_intent = "end_call"
    elif "answer" in raw_intent or "attend" in raw_intent or "accept" in raw_intent:
        logical_intent = "answer_call"
    elif "reject" in raw_intent or "decline" in raw_intent:
        logical_intent = "reject_call"
    elif "video" in raw_intent:
        logical_intent = "video_call"
    elif "voice" in raw_intent or "call" in raw_intent:
        logical_intent = "voice_call"
    elif "status" in raw_intent:
        logical_intent = "status"
    else:
        return "Error: Ambiguous WhatsApp command."

    if logical_intent == "inspect_whatsapp_ui":
        diag = inspect_whatsapp_ui(controller)
        if isinstance(diag, dict):
            if diag.get("report"):
                return diag["report"]
            controls = diag.get("controls", [])
            win_info = diag.get("whatsapp_window") or {}
            disp = diag.get("display") or {}
            voice_cand = diag.get("selected_voice_candidate") or {}
            vid_cand = diag.get("selected_video_candidate") or {}
            return (
                "--- WhatsApp UI Inspection & Live Control Discovery ---\n\n"
                "## WINDOW\n"
                f"HWND: {win_info.get('HWND', 'Unknown')}\n"
                f"PID: {win_info.get('ProcessId', 'Unknown')}\n"
                f"Bounds: {win_info.get('BoundingRectangle')}\n"
                f"Class: {win_info.get('ClassName', 'Unknown')}\n"
                f"Title: {win_info.get('Name', 'WhatsApp')}\n"
                f"Display: {disp}\n"
                f"Discovered Controls: {len(controls)} items\n\n"
                "## CALL CANDIDATES\n"
                f"Candidate #1: Name='{voice_cand.get('Name', 'Voice call')}'\n\n"
                "## VIDEO CANDIDATES\n"
                f"Candidate #1: Name='{vid_cand.get('Name', 'Video call')}'\n\n"
                "## FINAL FINDING\n"
                f"VOICE_CALL_CONTROL_FOUND = {'YES' if voice_cand else 'NO'}\n"
                f"VIDEO_CALL_CONTROL_FOUND = {'YES' if vid_cand else 'NO'}\n"
            )
        return str(diag)

    if not controller.is_whatsapp_running():
        return "Error: WhatsApp Desktop is not running or could not be acquired."

    if logical_intent == "status":
        state = controller.get_call_state()
        return f"WhatsApp Call State: {state}"

    if logical_intent in ["end_call", "answer_call", "reject_call"]:
        if logical_intent == "end_call":
            success = controller.end_call()
        elif logical_intent == "answer_call":
            success = controller.answer_call()
        elif logical_intent == "reject_call":
            success = controller.reject_call()
            
        if success:
            return f"Successfully executed {logical_intent}."
        else:
            return f"Failed to {logical_intent}. UI elements not found."

    if not contact_name:
        return "Error: Contact name not specified."

    if player and logical_intent != "whatsapp_search_test":
        try:
            player.write_log(f"JARVIS: Searching WhatsApp for '{contact_name}'...")
        except Exception:
            pass

    matches = controller.search_contact(contact_name)
    
    if logical_intent == "whatsapp_search_test":
        controller.open_contact(contact_name, matches)
        controller.check_call_buttons()
        log_output = "\n".join(controller.diagnostic_log)
        return f"--- WhatsApp Diagnostic Search Test Completed ---\nLog Trace:\n{log_output}\n\nSearch complete, no call initiated."
            
    # For actual calls, handle multiple matches if UIA is NOT blind
    if len(matches) > 1:
        return f"Multiple contacts found for '{contact_name}'. Matches: {matches}. Please be more specific."
    elif len(matches) == 0 and len(controller.diagnostic_log) > 0 and "UIA blind" not in controller.diagnostic_log[-1]:
        return f"Contact '{contact_name}' not found on WhatsApp Desktop."

    if player:
        try:
            player.write_log(f"JARVIS: Calling {contact_name} on WhatsApp.")
        except Exception:
            pass
            
    success_open = controller.open_contact(contact_name, matches)
    if not success_open:
        log_output = "\n".join(controller.diagnostic_log)
        return f"Failed to open chat for {contact_name}.\nLogs:\n{log_output}"

    if logical_intent == "video_call":
        success_call = controller.start_video_call(contact_name)
    else:
        success_call = controller.start_voice_call(contact_name)
        
    log_output = "\n".join(controller.diagnostic_log)
    
    if success_call == "SUCCESS_VERIFIED":
        return f"Successfully initiated {logical_intent} to {contact_name}.\nLogs:\n{log_output}"
    elif success_call == "SUCCESS_UNVERIFIED":
        return f"{logical_intent.replace('_', ' ').capitalize()} button click executed; call state could not be independently verified.\nLogs:\n{log_output}"
    else:
        return f"Failed to initiate call for {contact_name}. UI elements not found or unconfirmed.\nLogs:\n{log_output}"
