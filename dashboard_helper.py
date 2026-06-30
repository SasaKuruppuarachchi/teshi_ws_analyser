import os
import json
import re
import sys
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import threading
import atexit
import time

INDEX_FILENAME = "workspace_index.json"
LOCK_FILENAME = "workspace_lock.pid"
is_scanning = False
_cached_index = None
def get_roots():
    project_root = os.path.dirname(os.path.abspath(__file__))
    scan_root = os.path.dirname(project_root)
    return project_root, scan_root

def is_pid_running(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False

def check_and_create_lock():
    project_root, scan_root = get_roots()
    lock_path = os.path.join(project_root, LOCK_FILENAME)
    
    if os.path.exists(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    old_pid = int(content)
                    if is_pid_running(old_pid):
                        print(f"============================================================")
                        print(f"[Error] Another instance of Creator Space is already running!")
                        print(f"[Error] Workspace lock PID: {old_pid}")
                        print(f"[Error] Please close the other terminal window or instance.")
                        print(f"============================================================")
                        sys.exit(1)
        except SystemExit:
            raise
        except Exception as e:
            # If parsing fails or invalid file, print and ignore
            print(f"[Server] Invalid lock file detected: {e}. Overwriting...")
            
    my_pid = os.getpid()
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(my_pid))
        print(f"[Server] Lock file acquired (PID: {my_pid}).")
        atexit.register(remove_lock)
    except Exception as e:
        print(f"[Error] Failed to create lock file: {e}")

def remove_lock():
    project_root, scan_root = get_roots()
    lock_path = os.path.join(project_root, LOCK_FILENAME)
    if os.path.exists(lock_path):
        try:
            os.remove(lock_path)
            print("[Server] Lock file released.")
        except Exception as e:
            print(f"[Error] Failed to release lock file: {e}")

def get_dir_size(path):
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                if not entry.name.startswith("._"):
                    total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    except Exception:
        pass
    return total

def run_scan(force_dates=False):
    project_root, workspace_root = get_roots()
    print(f"[Scan Thread] Starting workspace scan at: {workspace_root}")
    
    existing_projects = {}
    if not force_dates:
        try:
            index_path = os.path.normpath(os.path.join(project_root, INDEX_FILENAME))
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    existing_projects = old_data.get("projects", {})
                print(f"[Scan Thread] Loaded {len(existing_projects)} projects from cache to optimize timestamping.")
        except Exception as e:
            print(f"[Scan Thread] Could not load cache: {e}")
    else:
        print("[Scan Thread] Force re-indexing chronological timeline: ignoring cached project dates.")
    
    report = {
        "summary": {
            "total_size_gb": 0,
            "total_files": 0,
            "total_folders": 0,
            "video_files": 0,
            "image_files": 0,
            "audio_files": 0,
            "clutter_files": 0,
            "clutter_size_mb": 0,
            "resolve_projects": 0,
            "premiere_projects": 0
        },
        "root_folders": [],
        "large_files": [],
        "typos": [],
        "clutter_summary": {
            "count": 0,
            "size_bytes": 0
        },
        "projects": {}
    }
    
    typo_checks = {
        r"\bEstonisa\b": "Estonia",
        r"\bProtugal\b": "Portugal",
        r"\bqoutes\b": "quotes",
        r"\bVillege\b": "Village",
        r"\bstawberry\b": "strawberry"
    }

    video_exts = {".mp4", ".mov", ".mkv", ".lrf", ".lrv", ".mpeg", ".avi"}
    image_exts = {".jpg", ".jpeg", ".heic", ".png", ".webp", ".gif", ".thm"}
    audio_exts = {".wav", ".mp3", ".m4a", ".aac"}
    
    all_files = []
    
    print("[Scan Thread] Checking parent directories...")
    for entry in os.scandir(workspace_root):
        if entry.is_dir():
            if entry.name.startswith(".") or entry.name.startswith("_") or entry.name == "teshi_ws_analyser":
                continue
            size_bytes = get_dir_size(entry.path)
            report["root_folders"].append({
                "name": entry.name,
                "path": entry.path,
                "size_gb": round(size_bytes / (1024**3), 2)
            })
            
            for pattern, correction in typo_checks.items():
                if re.search(pattern, entry.name, re.IGNORECASE):
                    report["typos"].append({
                        "path": entry.path,
                        "type": "Folder Name",
                        "current": entry.name,
                        "suggestion": re.sub(pattern, correction, entry.name, flags=re.IGNORECASE)
                    })

    print("[Scan Thread] Running directory walk (this can take several seconds)...")
    for root, dirs, files in os.walk(workspace_root):
        if "teshi_ws_analyser" in dirs:
            dirs.remove("teshi_ws_analyser")
        if any(part.startswith(".") for part in root.split(os.sep)):
            continue
            
        rel_path = os.path.relpath(root, workspace_root)
        parts = rel_path.split(os.sep)
        
        is_project = False
        project_key = None
        is_finished = False
        
        if len(parts) >= 2:
            parent = parts[0]
            if parent == "z_Finished" and len(parts) >= 3:
                country = parts[1]
                if country not in ["1_assets", "voicecuts"]:
                    project_key = os.path.join("z_Finished", parts[1], parts[2]).replace("\\", "/")
                    is_project = True
                    is_finished = True
            elif parent not in ["1_Assets", "1_Dummy_project", "1_Shorts", "z_Finished", "z_Other", "z_qoutes and dreams and recipes"]:
                project_key = os.path.join(parts[0], parts[1]).replace("\\", "/")
                is_project = True
            
        if is_project and project_key:
            if project_key not in report["projects"]:
                report["projects"][project_key] = {
                    "key": project_key,
                    "path": os.path.normpath(os.path.join(workspace_root, project_key)),
                    "name": os.path.basename(project_key),
                    "country": parts[1] if is_finished else parts[0],
                    "is_finished": is_finished,
                    "has_resolve": False,
                    "has_premiere": False,
                    "has_exports": False,
                    "has_thumbnail": False,
                    "has_footage": False,
                    "has_audio": False,
                    "min_mtime": None,
                    "max_mtime": None,
                    "footage_date_raw": 0,
                    "footage_date": None,
                    "mtime_checked_count": 0,
                    "subfolders": [],
                    "files_count": 0,
                    "size_gb": 0,
                    "project_files": []
                }
        
        for f in files:
            if f.startswith("._") and len(f) > 2:
                report["summary"]["clutter_files"] += 1
                try:
                    sz = os.path.getsize(os.path.join(root, f))
                    report["clutter_summary"]["size_bytes"] += sz
                except Exception:
                    pass
                continue
                
            full_path = os.path.join(root, f)
            report["summary"]["total_files"] += 1
            
            for pattern, correction in typo_checks.items():
                if re.search(pattern, f, re.IGNORECASE):
                    report["typos"].append({
                        "path": full_path,
                        "type": "File Name",
                        "current": f,
                        "suggestion": re.sub(pattern, correction, f, flags=re.IGNORECASE)
                    })
            
            try:
                file_size = os.path.getsize(full_path)
            except Exception:
                file_size = 0
                
            _, ext = os.path.splitext(f.lower())
            
            if ext in video_exts:
                report["summary"]["video_files"] += 1
                all_files.append((full_path, file_size, "Video"))
            elif ext in image_exts:
                report["summary"]["image_files"] += 1
                all_files.append((full_path, file_size, "Image"))
            elif ext in audio_exts:
                report["summary"]["audio_files"] += 1
                all_files.append((full_path, file_size, "Audio"))
            elif ext == ".drp":
                report["summary"]["resolve_projects"] += 1
                all_files.append((full_path, file_size, "DaVinci Resolve Project"))
                if project_key and project_key in report["projects"]:
                    report["projects"][project_key]["has_resolve"] = True
                    report["projects"][project_key]["project_files"].append({
                        "name": f,
                        "path": full_path,
                        "type": "DaVinci Resolve"
                    })
            elif ext == ".prproj":
                report["summary"]["premiere_projects"] += 1
                all_files.append((full_path, file_size, "Premiere Project"))
                if project_key and project_key in report["projects"]:
                    report["projects"][project_key]["has_premiere"] = True
                    report["projects"][project_key]["project_files"].append({
                        "name": f,
                        "path": full_path,
                        "type": "Premiere Pro"
                    })
            else:
                all_files.append((full_path, file_size, ext.upper()[1:] or "OTHER"))

            if project_key and project_key in report["projects"]:
                p_info = report["projects"][project_key]
                p_info["files_count"] += 1
                p_info["size_gb"] += file_size / (1024**3)
                
                # Check if we can reuse cached date
                old_p = existing_projects.get(project_key, {})
                if old_p.get("footage_date_raw"):
                    p_info["footage_date_raw"] = old_p["footage_date_raw"]
                    p_info["footage_date"] = old_p["footage_date"]
                elif ext in video_exts or ext in image_exts:
                    # Sample up to 10 files per project
                    if p_info.get("mtime_checked_count", 0) < 10:
                        try:
                            mtime = os.path.getmtime(full_path)
                            if p_info["min_mtime"] is None or mtime < p_info["min_mtime"]:
                                p_info["min_mtime"] = mtime
                            if p_info["max_mtime"] is None or mtime > p_info["max_mtime"]:
                                p_info["max_mtime"] = mtime
                            p_info["mtime_checked_count"] = p_info.get("mtime_checked_count", 0) + 1
                        except Exception:
                            pass

        for d in dirs:
            if d.startswith(".") or d.startswith("_") or d == "teshi_ws_analyser":
                continue
            report["summary"]["total_folders"] += 1
            if project_key and project_key in report["projects"]:
                report["projects"][project_key]["subfolders"].append(d)
                
            for pattern, correction in typo_checks.items():
                if re.search(pattern, d, re.IGNORECASE):
                    report["typos"].append({
                        "path": os.path.join(root, d),
                        "type": "Subfolder Name",
                        "current": d,
                        "suggestion": re.sub(pattern, correction, d, flags=re.IGNORECASE)
                    })

    print("[Scan Thread] Checking direct subfolder compliance...")
    for p_key, p_info in report["projects"].items():
        p_info["size_gb"] = round(p_info["size_gb"], 2)
        p_info["subfolders"] = list(set(p_info["subfolders"]))
        
        if p_info.get("footage_date_raw"):
            p_info.pop("min_mtime", None)
            p_info.pop("max_mtime", None)
            p_info.pop("mtime_checked_count", None)
        else:
            mtime_to_use = p_info.get("min_mtime") or p_info.get("max_mtime")
            if not mtime_to_use:
                try:
                    mtime_to_use = os.path.getmtime(p_info["path"])
                except Exception:
                    mtime_to_use = time.time()
                    
            p_info["footage_date_raw"] = mtime_to_use
            try:
                p_info["footage_date"] = time.strftime("%Y-%m-%d", time.localtime(mtime_to_use))
            except Exception:
                p_info["footage_date"] = time.strftime("%Y-%m-%d", time.localtime())
            
            p_info.pop("min_mtime", None)
            p_info.pop("max_mtime", None)
            p_info.pop("mtime_checked_count", None)
        
        proj_path = p_info["path"]
        p_info["metadata"] = {
            "youtube_video": "",
            "public_album": "",
            "private_album": ""
        }
        if os.path.exists(proj_path):
            try:
                for entry in os.scandir(proj_path):
                    if entry.is_dir():
                        low_name = entry.name.lower()
                        if "footage" in low_name or "raw" in low_name:
                            p_info["has_footage"] = True
                        if "audio" in low_name or "music" in low_name or "voice" in low_name:
                            p_info["has_audio"] = True
                        if "export" in low_name:
                            p_info["has_exports"] = True
                        if "thumbnail" in low_name:
                            p_info["has_thumbnail"] = True
            except Exception as e:
                print(f"[Scan Thread] Error scanning project compliance for {p_key}: {e}")
                
            # Parse project_metadata.json if it exists
            metadata_path = os.path.normpath(os.path.join(proj_path, "project_metadata.json"))
            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r", encoding="utf-8") as meta_file:
                        meta_data = json.load(meta_file)
                        p_info["metadata"]["youtube_video"] = meta_data.get("youtube_video", "").strip()
                        p_info["metadata"]["public_album"] = meta_data.get("public_album", "").strip()
                        p_info["metadata"]["private_album"] = meta_data.get("private_album", "").strip()
                except Exception as e:
                    print(f"[Scan Thread] Error reading metadata for {p_key}: {e}")

    print("[Scan Thread] Sorting large files...")
    all_files.sort(key=lambda x: x[1], reverse=True)
    for path, size, file_type in all_files[:30]:
        report["large_files"].append({
            "path": path,
            "name": os.path.basename(path),
            "rel_path": os.path.relpath(path, workspace_root).replace("\\", "/"),
            "size_gb": round(size / (1024**3), 3),
            "type": file_type
        })
        
    report["summary"]["clutter_size_mb"] = round(report["clutter_summary"]["size_bytes"] / (1024*1024), 2)
    total_non_clutter_bytes = sum(x[1] for x in all_files)
    report["summary"]["total_size_gb"] = round(total_non_clutter_bytes / (1024**3), 2)
    
    global _cached_index
    _cached_index = report
    
    index_path = os.path.normpath(os.path.join(project_root, INDEX_FILENAME))
    try:
        with open(index_path, "w", encoding="utf-8") as idx_file:
            json.dump(report, idx_file, indent=2)
        print(f"[Scan Thread] Successfully wrote updated index to: {index_path}")
    except Exception as e:
        print(f"[Scan Thread] Error writing index: {e}")
        
    return report

def load_index():
    global _cached_index
    project_root, workspace_root = get_roots()
    index_path = os.path.normpath(os.path.join(project_root, INDEX_FILENAME))
    
    if _cached_index is not None:
        return _cached_index
        
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as idx_file:
                _cached_index = json.load(idx_file)
                return _cached_index
        except Exception as e:
            print(f"[Server] Failed to read index cache file: {e}.")
            
    print("[Server] Index cache missing or locked. Initializing default empty structure and starting background scan...")
    _cached_index = {
        "summary": {
            "total_size_gb": 0.0,
            "total_files": 0,
            "total_folders": 0,
            "video_files": 0,
            "image_files": 0,
            "audio_files": 0,
            "clutter_files": 0,
            "clutter_size_mb": 0.0,
            "resolve_projects": 0,
            "premiere_projects": 0
        },
        "root_folders": [],
        "large_files": [],
        "typos": [],
        "clutter_summary": {"count": 0, "size_bytes": 0},
        "projects": {}
    }
    trigger_background_scan()
    return _cached_index

def trigger_background_scan(force_dates=False):
    global is_scanning
    if is_scanning:
        print("[Server] Background scan already running. Request ignored.")
        return
        
    is_scanning = True
    print(f"[Server] Spawning background scan thread (force_dates={force_dates})...")
    def run():
        global is_scanning
        try:
            run_scan(force_dates=force_dates)
        except Exception as e:
            print(f"[Scan Thread] Error: {e}")
        finally:
            is_scanning = False
            print("[Server] Background scan thread finished.")
            
    threading.Thread(target=run, daemon=True).start()

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
        
    def do_GET(self):
        project_root, workspace_root = get_roots()
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path == "/":
            html_path = os.path.join(project_root, "workspace_dashboard.html")
            print(f"[Server] GET / -> Serving workspace_dashboard.html")
            if os.path.exists(html_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(html_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Dashboard HTML not found.")
                
        elif parsed_path.path.startswith("/static/"):
            rel_file_path = parsed_path.path[len("/static/"):]
            rel_file_path = urllib.parse.unquote(rel_file_path)
            abs_file_path = os.path.abspath(os.path.join(workspace_root, rel_file_path))
            if abs_file_path.startswith(workspace_root) and os.path.exists(abs_file_path) and os.path.isfile(abs_file_path):
                ext = os.path.splitext(abs_file_path)[1].lower()
                content_types = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".svg": "image/svg+xml",
                    ".mp4": "video/mp4",
                    ".mov": "video/quicktime",
                    ".mp3": "audio/mpeg",
                    ".wav": "audio/wav",
                    ".css": "text/css",
                    ".js": "application/javascript"
                }
                content_type = content_types.get(ext, "application/octet-stream")
                
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(abs_file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "File not found.")
                
        elif parsed_path.path == "/api/data":
            query = urllib.parse.parse_qs(parsed_path.query)
            is_poll = "poll" in query
            
            print(f"[Server] GET /api/data (poll={is_poll}) -> Loading cached index...")
            try:
                data = load_index()
                if not is_poll and not is_scanning:
                    trigger_background_scan()
                    
                data["is_scanning"] = is_scanning
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
                
        else:
            self.send_error(404)
            
    def do_POST(self):
        project_root, workspace_root = get_roots()
        parsed_path = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        
        response = {"success": False, "message": ""}
        
        try:
            if parsed_path.path == "/api/scan":
                query = urllib.parse.parse_qs(parsed_path.query)
                force_dates = "force_dates" in query
                if not force_dates and post_data:
                    try:
                        params = json.loads(post_data.decode("utf-8"))
                        force_dates = params.get("force_dates", False)
                    except Exception:
                        pass
                        
                print(f"[Server] POST /api/scan (force_dates={force_dates}) -> Manual rescan requested.")
                trigger_background_scan(force_dates=force_dates)
                response = {
                    "success": True,
                    "message": "Scan started in background."
                }
                
            elif parsed_path.path == "/api/save-metadata":
                params = json.loads(post_data.decode("utf-8")) if post_data else {}
                project_key = params.get("project_key", "").strip()
                
                print(f"[Server] POST /api/save-metadata -> Saving metadata for: {project_key}")
                if not project_key:
                    raise Exception("Missing project key.")
                    
                data = load_index()
                project = data.get("projects", {}).get(project_key)
                if not project:
                    raise Exception(f"Project not found: {project_key}")
                    
                proj_path = project["path"]
                metadata_path = os.path.normpath(os.path.join(proj_path, "project_metadata.json"))
                
                meta_to_save = {
                    "youtube_video": params.get("youtube_video", "").strip(),
                    "public_album": params.get("public_album", "").strip(),
                    "private_album": params.get("private_album", "").strip()
                }
                
                os.makedirs(proj_path, exist_ok=True)
                with open(metadata_path, "w", encoding="utf-8") as meta_file:
                    json.dump(meta_to_save, meta_file, indent=2)
                    
                print(f"[Server] Saved project metadata to: {metadata_path}")
                
                global _cached_index
                if _cached_index and "projects" in _cached_index and project_key in _cached_index["projects"]:
                    _cached_index["projects"][project_key]["metadata"] = meta_to_save
                    
                trigger_background_scan()
                response = {
                    "success": True,
                    "message": "Metadata saved successfully."
                }
                
            elif parsed_path.path == "/api/clean-clutter":
                print("[Server] POST /api/clean-clutter -> Cleaning macOS clutter files...")
                deleted_count = 0
                deleted_bytes = 0
                for root, dirs, files in os.walk(workspace_root):
                    if "teshi_ws_analyser" in dirs:
                        dirs.remove("teshi_ws_analyser")
                    for f in files:
                        if f.startswith("._") and len(f) > 2:
                            full_path = os.path.join(root, f)
                            try:
                                sz = os.path.getsize(full_path)
                                os.remove(full_path)
                                deleted_count += 1
                                deleted_bytes += sz
                            except Exception:
                                pass
                print(f"[Server] Cleanup finished. Deleted {deleted_count} files.")
                trigger_background_scan()
                response = {
                    "success": True, 
                    "message": f"Successfully deleted {deleted_count} macOS metadata files ({round(deleted_bytes/(1024*1024), 2)} MB freed). Background scan started."
                }
                
            elif parsed_path.path == "/api/fix-typos":
                print("[Server] POST /api/fix-typos -> Renaming folders with typos...")
                data = load_index()
                fixed_count = 0
                errors = []
                
                typos_list = data.get("typos", [])
                typos_list.sort(key=lambda x: len(x["path"]), reverse=True)
                
                for t in typos_list:
                    old_path = t["path"]
                    if not os.path.exists(old_path):
                        continue
                    dir_name = os.path.dirname(old_path)
                    new_path = os.path.join(dir_name, t["suggestion"])
                    
                    try:
                        os.rename(old_path, new_path)
                        fixed_count += 1
                    except Exception as e:
                        errors.append(f"Failed to rename {old_path}: {str(e)}")
                print(f"[Server] Spelling renames complete. Fixed {fixed_count} typos.")
                trigger_background_scan()
                response = {
                    "success": True,
                    "message": f"Fixed {fixed_count} typos. Errors: {len(errors)}. Background scan started.",
                    "errors": errors
                }
                
            elif parsed_path.path == "/api/create-project":
                params = json.loads(post_data.decode("utf-8")) if post_data else {}
                project_name = params.get("name", "").strip()
                parent_folder = params.get("parent", "").strip()
                
                print(f"[Server] POST /api/create-project -> Initializing project '{project_name}' inside '{parent_folder}'...")
                if not project_name or not parent_folder:
                    raise Exception("Missing project name or parent folder.")
                    
                target_dir = os.path.normpath(os.path.join(workspace_root, parent_folder, project_name))
                if os.path.exists(target_dir):
                    raise Exception("Project folder already exists!")
                    
                subfolders = ["01_Footage", "02_Audio", "03_Projects", "04_Assets", "05_Exports", "06_Thumbnails"]
                os.makedirs(target_dir, exist_ok=True)
                for sf in subfolders:
                    os.makedirs(os.path.normpath(os.path.join(target_dir, sf)), exist_ok=True)
                
                trigger_background_scan()
                response = {
                    "success": True,
                    "message": f"Successfully created project '{project_name}' inside '{parent_folder}'. Background scan started."
                }
                
            elif parsed_path.path == "/api/create-and-open-folder":
                params = json.loads(post_data.decode("utf-8")) if post_data else {}
                project_key = params.get("project_key", "").strip()
                folder_type = params.get("folder_type", "").strip()
                
                print(f"[Server] POST /api/create-and-open-folder -> Handling {folder_type} for project '{project_key}'...")
                if not project_key or not folder_type:
                    raise Exception("Missing project key or folder type.")
                
                folder_map = {
                    "footage": "01_Footage",
                    "audio": "02_Audio",
                    "exports": "05_Exports",
                    "thumbnail": "06_Thumbnails"
                }
                
                if folder_type not in folder_map:
                    raise Exception(f"Invalid folder type: {folder_type}")
                    
                folder_name = folder_map[folder_type]
                folder_path = os.path.normpath(os.path.join(workspace_root, project_key, folder_name))
                
                created = False
                if not os.path.exists(folder_path):
                    os.makedirs(folder_path, exist_ok=True)
                    created = True
                
                try:
                    if os.name == 'nt':
                        os.startfile(folder_path)
                    else:
                        subprocess.Popen(["open", folder_path])
                except Exception as e:
                    print(f"[Server] Error opening folder: {e}")
                
                trigger_background_scan()
                
                action_text = "created and opened" if created else "opened"
                response = {
                    "success": True,
                    "message": f"Successfully {action_text} folder '{folder_name}'. Background scan started."
                }
                
            elif parsed_path.path == "/api/open-file":
                params = json.loads(post_data.decode("utf-8")) if post_data else {}
                file_path = params.get("path", "").strip()
                print(f"[Server] POST /api/open-file -> Opening '{file_path}'...")
                if not file_path:
                    raise Exception("Missing file path.")
                
                abs_file_path = os.path.abspath(file_path)
                if not abs_file_path.startswith(workspace_root):
                    raise Exception("Access denied: File is outside workspace.")
                if not os.path.exists(abs_file_path):
                    raise Exception("File does not exist.")
                
                try:
                    if os.name == 'nt':
                        os.startfile(abs_file_path)
                    else:
                        subprocess.Popen(["open", abs_file_path])
                except Exception as e:
                    print(f"[Server] Error opening file: {e}")
                    raise Exception(f"Failed to open file: {str(e)}")
                    
                response = {
                    "success": True,
                    "message": f"Successfully opened: {os.path.basename(abs_file_path)}"
                }
                
            elif parsed_path.path == "/api/shutdown":
                print("[Server] POST /api/shutdown -> Shutdown requested.")
                response = {
                    "success": True,
                    "message": "Instance terminated successfully. You can close this window."
                }
                self.wfile.write(json.dumps(response).encode("utf-8"))
                
                def close_app():
                    time.sleep(0.5)
                    print("[Server] Releasing lock and exiting.")
                    remove_lock()
                    # Terminate process cleanly
                    os._exit(0)
                    
                threading.Thread(target=close_app, daemon=True).start()
                return
                
        except Exception as e:
            print(f"[Server] Error handling POST request: {e}")
            response = {"success": False, "message": str(e)}
            
        self.wfile.write(json.dumps(response).encode("utf-8"))
        
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def main():
    global http_server
    
    # Enforce single instance rule per workspace
    check_and_create_lock()
    
    port = 8000
    while port < 8100:
        try:
            http_server = HTTPServer(('localhost', port), DashboardHandler)
            print(f"============================================================")
            print(f"[Server] Content Creator Workspace Dashboard Server started!")
            print(f"[Server] Listening on: http://localhost:{port}")
            print(f"============================================================")
            http_server.serve_forever()
            break
        except OSError:
            port += 1

if __name__ == "__main__":
    main()
