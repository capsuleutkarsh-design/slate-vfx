import os
import json
import cv2
import logging
import re
import subprocess
import numpy as np
from pathlib import Path

from ut_vfx.utils.resource_manager import ResourcePathManager
from ut_vfx.utils.media_capabilities import IMAGE_EXTENSIONS
from shutil import which

class ProxyManagerMeta:
    def __init__(self):
        self.ffmpeg_path = ResourcePathManager.get_ffmpeg_path()
        self.ffprobe_path = ResourcePathManager.get_ffprobe_path()
        if not self._tool_available(self.ffmpeg_path):
            self.ffmpeg_path = None
        if not self._tool_available(self.ffprobe_path):
            self.ffprobe_path = None

    @staticmethod
    def _tool_available(tool_path: str) -> bool:
        if not tool_path:
            return False
        path = Path(str(tool_path))
        if path.exists():
            return True
        return which(str(tool_path)) is not None

proxy_manager_meta = ProxyManagerMeta()

class SmartMetadataManager:
    _FFPROBE_TIMEOUT_SEC = max(10, int(os.getenv("UTVFX_FFPROBE_TIMEOUT", "15")))
    _FFMPEG_FALLBACK_TIMEOUT_SEC = max(5, int(os.getenv("UTVFX_FFMPEG_PROBE_TIMEOUT", "12")))

    @staticmethod
    def classify_category(file_path: Path):
        """
        Determines the Asset Category based on filename/path keywords using Regex.
        Returns 'Uncategorized' if no match found, or the matched category.
        """
        name = file_path.name.lower()
        parent = file_path.parent.name.lower()
        full_str = f"{parent}/{name}"

        # 1. TEXTURES & MATERIALS
        if re.search(r'(albedo|diffuse|specular|roughness|normal|bump|displacement|ao|ambient|texture|tex|matlib|material)', full_str):
            return "Textures"
        
        # 2. HDRI / LIGHTING
        img_ext = file_path.suffix.lower()
        if re.search(r'(hdri|env|pano|skydome|lightprobe|exr|hdr)', full_str) and img_ext in IMAGE_EXTENSIONS:
             return "HDRI"
             
        # 3. STOCK FOOTAGE - DETAILED CATEGORIZATION
        if re.search(r'(fire|flame|torch|ignite|burn)', full_str): return "Fire"
        if re.search(r'(smoke|steam|wisps|fume)', full_str): return "Smoke"
        if re.search(r'(explosion|blast|detonation|pyro|bomb)', full_str): return "Explosions"
        if re.search(r'(muzzle|gunshot|flash|weapon)', full_str): return "Muzzle Flashes"
        if re.search(r'(spark|ember|arc|electric)', full_str): return "Sparks"
        if re.search(r'(cloud|fog|mist|haze|atmosphere)', full_str): return "Atmosphere"
        if re.search(r'(blood|gore|splatter|wound)', full_str): return "Blood"
        if re.search(r'(debris|dust|shatter|gravel|dirt|ground)', full_str): return "Particles"
        if re.search(r'(water|splash|liquid|rain|ocean)', full_str): return "Liquids"
        if re.search(r'(magic|energy|beam|laser|sci-fi)', full_str): return "Magic/Sci-Fi"
        
        # Generic Fallback
        if re.search(r'(element|stock|vfx|footage)', full_str):
            return "Stock Elements"
            
        # 4. REFERENCE
        if re.search(r'(ref|reference|plate|raw|dailies|scan|photo)', full_str):
            return "References"

        # 5. 3D MODELS
        if file_path.suffix.lower() in ['.fbx', '.obj', '.abc', '.usd', '.usda', '.usdc']:
            return "3D Models"
            
        # 6. AUDIO
        if file_path.suffix.lower() in ['.wav', '.mp3', '.ogg', '.flac']:
             return "Sound FX"

        # Fallback to Parent Folder Name (Capitalized)
        return file_path.parent.name.capitalize()

    @staticmethod
    def get_smart_tags(file_path: Path):
        # FIX: Ignore macOS resource fork files
        if file_path.name.startswith("._"):
            return "Uncategorized", []

        # Use new classifier
        primary_category = SmartMetadataManager.classify_category(file_path)
        
        tags = set()
        tags.add(primary_category)
        
        clean_name = file_path.stem.replace('_', ' ').replace('.', ' ').replace('-', ' ')
        # Split by typical separators and CamelCase
        words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+', clean_name)
            
        for w in words:
            w = w.strip()
            if len(w) > 2 and not w.isdigit():
                tags.add(w)
                
        tag_list = list(tags)
        # Ensure category is first
        if primary_category in tag_list:
            tag_list.remove(primary_category)
        tag_list.insert(0, primary_category)
        return primary_category, tag_list

    @staticmethod
    def extract_tech_metadata(file_path: str):
        """
        Extracts metadata using FFprobe (JSON) and adds duration fallback.
        """
        meta = {"width": 0, "height": 0, "fps": 0.0, "duration_sec": 0.0}
        
        ffprobe_path = proxy_manager_meta.ffprobe_path
        ffmpeg_path = proxy_manager_meta.ffmpeg_path
        
        # FIX: Ignore macOS resource fork files (._*)
        if Path(file_path).name.startswith("._"):
             return meta

        if proxy_manager_meta._tool_available(ffprobe_path):
            try:
                # 1. FFprobe JSON Analysis
                cmd = [
                    ffprobe_path, 
                    "-v", "quiet", 
                    "-print_format", "json", 
                    "-show_format", 
                    "-show_streams", 
                    str(file_path)
                ]
                
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                result = subprocess.run(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True, 
                    encoding='utf-8', 
                    errors='replace',
                    startupinfo=startupinfo,
                    timeout=SmartMetadataManager._FFPROBE_TIMEOUT_SEC
                )
                
                data = json.loads(result.stdout)
                
                # A. Container/Format Info
                if 'format' in data and 'duration' in data['format']:
                    meta["duration_sec"] = float(data['format']['duration'])
                    
                # B. Stream Analysis - Select the best video stream
                streams_found = []
                for stream in data.get('streams', []):
                    if stream.get('codec_type') == 'video':
                        codec = stream.get('codec_name', 'unknown').lower()
                        w = stream.get('width', 0)
                        h = stream.get('height', 0)
                        
                        if w == 0 or h == 0: continue

                        pixels = w * h
                        is_image_codec = codec in ['png', 'mjpeg', 'bmp', 'tiff', 'gif', 'webp']
                        score = pixels
                        if not is_image_codec:
                            score *= 10
                            
                        streams_found.append({
                            "w": w, "h": h,
                            "score": score,
                            "r_frame_rate": stream.get('r_frame_rate', '0/1'),
                            "codec": codec # Capture codec name
                        })

                if streams_found:
                    streams_found.sort(key=lambda x: x["score"], reverse=True)
                    winner = streams_found[0]
                    
                    meta["width"] = winner["w"]
                    meta["height"] = winner["h"]
                    meta["codec"] = winner.get("codec", "unknown")
                    
                    try:
                        num, den = map(int, winner["r_frame_rate"].split('/'))
                        meta["fps"] = num / den if den > 0 else 0.0
                    except Exception:
                        meta["fps"] = 0.0

            except Exception as e:
                logging.warning(f"FFprobe JSON scan error for {Path(file_path).name}: {e}")

        # 2. CRITICAL DURATION & VIDEO INFO FALLBACK (Simple FFmpeg command)
        # Run if ffprobe failed OR missed critical data
        if (meta["duration_sec"] == 0 or meta["width"] == 0) and proxy_manager_meta._tool_available(ffmpeg_path):
             try:
                cmd = [ffmpeg_path, "-i", str(file_path)]
                
                startupinfo = None
                creationflags = 0
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    creationflags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW

                result = subprocess.run(
                    cmd, 
                    stderr=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    text=True, 
                    encoding='utf-8', 
                    errors='replace',
                    timeout=SmartMetadataManager._FFMPEG_FALLBACK_TIMEOUT_SEC,
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )
                output = result.stderr
                
                # Debug logging
                # logging.info(f"FFmpeg Output for {Path(file_path).name}:\n{output}")

                # A. Duration - Handle various formats
                # Duration: 00:00:05.12, start: 0.000000, bitrate: 14785 kb/s
                dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
                if dur_match:
                    h, m, s = dur_match.groups()
                    meta["duration_sec"] = int(h)*3600 + int(m)*60 + float(s)

                # B. Resolution & FPS - Enhance Regex
                # Stream #0:0(eng): Video: h264 (Main), yuv420p(tv, bt709, progressive), 1920x1080 [SAR 1:1 DAR 16:9], 23.98 fps...
                # Look for "Video:" then later "WxH"
                
                # Scan for video stream line
                video_lines = [line for line in output.split('\n') if 'Video:' in line]
                if video_lines:
                    v_line = video_lines[0]
                    
                    # Resolution: 1920x1080
                    res_match = re.search(r"(\d{3,5})x(\d{3,5})", v_line)
                    if res_match:
                        meta["width"] = int(res_match.group(1))
                        meta["height"] = int(res_match.group(2))
                    
                    # FPS: 23.98 fps
                    fps_match = re.search(r"(\d+(?:\.\d+)?)\s+fps", v_line)
                    if fps_match:
                         meta["fps"] = float(fps_match.group(1))
                    
                    # Codec
                    # Video: h264 (Main) ...
                    codec_match = re.search(r"Video:\s*([^,\s]+)", v_line)
                    if codec_match:
                        meta["codec"] = codec_match.group(1).lower()

             except Exception as e:
                 logging.warning(f"Simple FFmpeg duration fallback failed: {e}")


        # 3. OpenCV Fallback for FPS/Resolution (DISABLE FOR STABILITY)
        # Using cv2.VideoCapture crashes on H.265 files without system codecs.
        # Since we have FFMPEG now (via proper lookup), we don't need this risky fallback.
        # if meta["width"] == 0 or meta["fps"] == 0:
        #      # DISABLED to prevent crash
        #      pass

        return meta

    @staticmethod
    def format_resolution(w, h):
        if w == 0: return "Unknown"
        if w >= 3840: return f"4K ({w}x{h})"
        if w >= 2048: return f"2K ({w}x{h})"
        if w >= 1920: return f"HD ({w}x{h})"
        if w >= 1280: return f"720p ({w}x{h})"
        return f"{w}x{h}"

    @staticmethod
    def extract_visual_tags(thumb_path: str):
        """
        Analyzes the thumbnail to generate smart visual tags.
        Returns a list of tags: ['Bright', 'Dark', 'Warm', 'Cold', 'Green Screen', 'Blue Screen']
        """
        if not thumb_path or not os.path.exists(thumb_path):
            return []

        tags = []
        try:
            # Read image (UNICODE SAFE)
            # cv2.imread fails on non-ASCII paths. Use numpy fromfile + imdecode.
            stream = np.fromfile(str(thumb_path), dtype=np.uint8)
            img = cv2.imdecode(stream, cv2.IMREAD_COLOR)
            if img is None: return []

            # Resize to small 64x64 for speed
            img = cv2.resize(img, (64, 64), interpolation=cv2.INTER_AREA)

            # Convert to HSV
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            h, s, v = cv2.split(hsv)

            # 1. Brightness Analysis
            avg_v = np.mean(v)
            if avg_v < 60: tags.append("Dark")
            elif avg_v > 190: tags.append("Bright")

            # 2. Keying Color Analysis (Green/Blue Screen)
            # Green in OpenCV Hue (0-180) is approx 40-80
            # Blue is approx 100-140
            # We check if >40% of the image is in that range AND high saturation
            
            # Mask for Green
            green_mask = cv2.inRange(hsv, (35, 100, 50), (85, 255, 255))
            green_ratio = np.sum(green_mask > 0) / (64*64)
            if green_ratio > 0.4: tags.append("Green Screen")

            # Mask for Blue
            blue_mask = cv2.inRange(hsv, (95, 120, 50), (135, 255, 255)) 
            blue_ratio = np.sum(blue_mask > 0) / (64*64)
            if blue_ratio > 0.4: tags.append("Blue Screen")

            # 3. Temperature Analysis (Warm vs Cool)
            # Warm: Red (0-15, 165-180), Orange, Yellow
            # Cool: Blue, Cyan (85-135)
            # We calculate mean hue of saturated pixels
            
            sat_mask = s > 50  # Only consider colorful pixels
            if np.sum(sat_mask) > 100: # Ensure we have enough color
                mean_h = np.mean(h[sat_mask])
                if (0 <= mean_h < 25) or (155 <= mean_h <= 180):
                    tags.append("Warm")
                elif (90 <= mean_h < 135):
                    tags.append("Cold")

        except Exception as e:
            logging.warning(f"Visual Analysis failed: {e}")
        
        return tags
