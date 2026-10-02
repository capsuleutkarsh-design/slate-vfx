import os
import json
import logging
import re
import subprocess
import numpy as np
from pathlib import Path

from slate.utils.resource_manager import ResourcePathManager
from slate.utils.media_capabilities import IMAGE_EXTENSIONS
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
    _FFPROBE_TIMEOUT_SEC = max(10, int(os.getenv("Slate_FFPROBE_TIMEOUT", "15")))
    _FFMPEG_FALLBACK_TIMEOUT_SEC = max(5, int(os.getenv("Slate_FFMPEG_PROBE_TIMEOUT", "12")))

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

    # Single pictures whose size Qt can read straight from the file header.
    # Running ffprobe - a separate process - on each of them made an ingest of
    # tiny stills take over a second apiece (MED-029), and ffprobe reports a
    # still as 25 fps lasting 0.04 s, which the inspector then showed (MED-020).
    _QT_STILLS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".tga"}
    # Pictures that are stills even when ffprobe has to read them.
    _STILL_EXTENSIONS = set(IMAGE_EXTENSIONS)

    @staticmethod
    def _still_size(file_path: str):
        """(width, height, format) from the file header, or None."""
        try:
            from PySide6.QtGui import QImageReader
            reader = QImageReader(str(file_path))
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                fmt = bytes(reader.format()).decode("ascii", "ignore").lower()
                return size.width(), size.height(), fmt or Path(file_path).suffix.lstrip(".").lower()
        except Exception as exc:
            logging.debug("Header read failed for %s: %s", Path(file_path).name, exc)
        return None

    @staticmethod
    def extract_tech_metadata(file_path: str):
        """
        Width, height, fps, duration and codec of a file.

        Stills come back with is_still True, fps 0 and duration 0 - a picture
        has no frame rate, whatever ffprobe says about it. Their size is read
        from the header without starting a process; only movies and the
        formats Qt cannot read (EXR, DPX, HDR) go to ffprobe.
        """
        meta = {"width": 0, "height": 0, "fps": 0.0, "duration_sec": 0.0}

        ffprobe_path = proxy_manager_meta.ffprobe_path
        ffmpeg_path = proxy_manager_meta.ffmpeg_path

        # FIX: Ignore macOS resource fork files (._*)
        if Path(file_path).name.startswith("._"):
             return meta

        suffix = Path(str(file_path)).suffix.lower()
        is_still = suffix in SmartMetadataManager._STILL_EXTENSIONS
        if is_still:
            meta["is_still"] = True
            if suffix in SmartMetadataManager._QT_STILLS:
                found = SmartMetadataManager._still_size(file_path)
                if found:
                    meta["width"], meta["height"], meta["codec"] = found
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
        needs_fallback = meta["width"] == 0 or (meta["duration_sec"] == 0 and not is_still)
        if needs_fallback and proxy_manager_meta._tool_available(ffmpeg_path):
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


        if is_still:
            # ffprobe reads a picture as a one-frame 25 fps "video"; a still has
            # neither a rate nor a length.
            meta["fps"] = 0.0
            meta["duration_sec"] = 0.0
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
        What a picture looks like, as tags: Dark, Bright, Warm, Cold,
        Green Screen, Blue Screen.

        Rebuilt without OpenCV (MED-005). The first version was switched off
        "for stability" and never came back, so the gallery's Visual filter
        could never match anything. It now reads the small cached thumbnail
        through Qt - safe on a worker thread, and the same decoder the gallery
        already uses - and does the colour maths in numpy on 64x64 pixels.

        Warm and cold are decided by how many coloured pixels fall in the warm
        and cool hue ranges, not by an average hue: reds sit at both ends of
        the hue circle, and averaging them called a red flame "cold".
        """
        from .proxy_manager import ProxyManager
        if not thumb_path or not ProxyManager.exists(thumb_path):
            return []
        try:
            from PySide6.QtCore import Qt as _Qt
            from PySide6.QtGui import QImage
            image = QImage(str(thumb_path))
            if image.isNull():
                return []
            image = image.scaled(64, 64, _Qt.AspectRatioMode.IgnoreAspectRatio,
                                 _Qt.TransformationMode.SmoothTransformation)
            image = image.convertToFormat(QImage.Format.Format_RGB888)
            stride = image.bytesPerLine()
            raw = np.frombuffer(image.constBits(), dtype=np.uint8,
                                count=stride * image.height())
            rgb = raw.reshape(image.height(), stride)[:, :image.width() * 3]
            rgb = rgb.reshape(image.height(), image.width(), 3).astype(np.float32) / 255.0
            return SmartMetadataManager.visual_tags_from_rgb(rgb)
        except Exception as e:
            logging.warning(f"Visual Analysis failed: {e}")
            return []

    @staticmethod
    def visual_tags_from_rgb(rgb):
        """The tag rules on an (h, w, 3) float array in 0..1."""
        tags = []
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        v = rgb.max(axis=2)
        low = rgb.min(axis=2)
        chroma = v - low
        s = np.where(v > 0, chroma / np.maximum(v, 1e-6), 0.0)
        # Hue in degrees, 0..360.
        safe = np.maximum(chroma, 1e-6)
        hue = np.where(v == r, ((g - b) / safe) % 6.0,
              np.where(v == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0)) * 60.0
        hue = np.where(chroma > 0, hue, 0.0)
        total = float(rgb.shape[0] * rgb.shape[1]) or 1.0

        # Brightness as the eye sees it (Rec.709 luma), not HSV value: a
        # saturated green screen is not a "bright" picture.
        brightness = float((0.2126 * r + 0.7152 * g + 0.0722 * b).mean()) * 255.0
        if brightness < 60:
            tags.append("Dark")
        elif brightness > 190:
            tags.append("Bright")

        green = (hue >= 70) & (hue <= 170) & (s >= 100 / 255.0) & (v >= 50 / 255.0)
        if green.sum() / total > 0.4:
            tags.append("Green Screen")
        blue = (hue >= 190) & (hue <= 270) & (s >= 120 / 255.0) & (v >= 50 / 255.0)
        if blue.sum() / total > 0.4:
            tags.append("Blue Screen")

        coloured = (s > 50 / 255.0) & (v > 40 / 255.0)
        n = int(coloured.sum())
        if n > max(100, total * 0.05):
            warm = (((hue < 50) | (hue >= 310)) & coloured).sum() / n
            cold = (((hue >= 180) & (hue < 270)) & coloured).sum() / n
            if warm > 0.5:
                tags.append("Warm")
            elif cold > 0.5:
                tags.append("Cold")
        return tags
