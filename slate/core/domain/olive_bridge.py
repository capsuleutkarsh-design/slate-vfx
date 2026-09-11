
"""
Olive Bridge - Generates Project Files (.ovexml) for Olive Video Editor 0.2
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
import time
import random
import math
from pathlib import Path
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class OliveBridge:
    """
    Generates .ovexml project files for Olive Video Editor.
    Reverses the Node-Graph structure:
    Footage -> Transform -> Clip -> Track -> Sequence
    """
    
    def __init__(self):
        self._ptr_base = int(time.time() * 1000) * 1000000
        self._ptr_counter = 0
        self.root_ptr = self._generate_ptr()
        self.sequence_ptr = self._generate_ptr()
        self.nodes = []
        self.connections = []
        
        # Track active render/scan tracks
        # Format: (track_ptr, list_of_clips)
        self.tracks: Dict[str, List[Dict]] = {} 
        self.video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".mxf", ".webm"}
        self._max_proxy_scan_entries = 200

    def _generate_ptr(self) -> str:
        """
        A pointer that is unique within this project file.

        Olive wires nodes together by these, so two nodes sharing one means a
        clip pointing at the wrong footage. The old version added a random
        number under ten thousand to a millisecond timestamp - which collides
        readily, because a draw a millisecond later with a smaller random part
        lands on the same value. A counter cannot.
        """
        self._ptr_counter = getattr(self, "_ptr_counter", 0) + 1
        return f"{self._ptr_base}{self._ptr_counter:06d}"

    # Layer 1 is the plate, and each department sits on its own layer beneath
    # it, so a render is always directly under the scan it was made from.
    DEFAULT_TRACKS = (("Scans", "scan"), ("Renders", "render"))
    DEPARTMENT_TRACKS = (("Scan", "scan"), ("Comp", "comp"),
                         ("Prep", "prep"), ("Deage", "deage"))

    def generate_project(self, shots: List, output_path: Path,
                         prefer_proxy_media: bool = True, tracks=None) -> bool:
        """
        Main entry point. Converts a list of shots to .ovexml.

        ``tracks`` is an ordered ``(track label, stream key)`` sequence: track
        one is laid out first and every later track sits beneath it. A shot
        supplies a stream by having a ``<key>_path`` attribute, so the same
        code builds the old two-track scan/render lineup and the department
        stack the dashboard asks for.
        """
        try:
            # Reset internal state for each export.
            # A fresh base per export, so two files never share pointers.
            self._ptr_base = int(time.time() * 1000) * 1000000
            self._ptr_counter = 0
            self.root_ptr = self._generate_ptr()
            self.sequence_ptr = self._generate_ptr()
            self.nodes = []
            self.connections = []
            self.tracks = {}

            # 1. Create Root Project Structure
            root_xml = ET.Element("olive", version="230220", url=str(output_path))
            project = ET.SubElement(root_xml, "project")
            proj_inner = ET.SubElement(project, "project", version="1")
            
            # 2. Define Root Folder Node
            self._add_root_folder_node()
            
            # 3. Create Sequence Node
            self._add_sequence_node()
            
            # 4. Create the tracks, in the order they were asked for.
            layout = list(tracks or self.DEFAULT_TRACKS)
            track_ptrs = {key: self._add_track_node(label)
                          for label, key in layout}

            # 5. Process Shots
            current_time = 0

            for shot in shots:
                # Calculate Duration
                if hasattr(shot, 'get_frame_count'):
                    duration = shot.get_frame_count()
                elif hasattr(shot, 'frame_range') and shot.frame_range:
                    duration = shot.frame_range[1] - shot.frame_range[0] + 1
                else:
                    duration = getattr(shot, 'duration_frames', 100)
                fps = float(getattr(shot, "fps", 24.0) or 24.0)
                
                # Every stream this shot has goes on its own track, all at the
                # same position on the timeline: a comp sits directly beneath
                # the plate it was made from, which is the whole point of the
                # layer stack.
                for _label, key in layout:
                    if not getattr(shot, f"{key}_path", None):
                        continue

                    source = self._resolve_olive_media_path(
                        shot, key, prefer_proxy_media)
                    if not source:
                        logger.warning(
                            "Skipping %s for %s (no usable media)",
                            key, getattr(shot, 'name', 'Unknown'))
                        continue

                    self._add_clip_to_track(
                        track_ptr=track_ptrs[key],
                        file_path=source,
                        start_time=current_time,
                        duration=duration,
                        fps=fps,
                        label=f"{shot.name}_{key}",
                    )

                current_time += duration

            # 6. Build <nodes> section
            nodes_elem = ET.SubElement(proj_inner, "nodes")
            for node in self.nodes:
                nodes_elem.append(node)

            # 7. Add Settings & Layout (Boilerplate)
            self._add_settings(proj_inner)
            self._add_layout(project)

            # 8. Write to file
            xml_str = minidom.parseString(ET.tostring(root_xml)).toprettyxml(indent="    ")
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(xml_str)
                
            return True

        except Exception as e:
            logger.error(f"Olive Export Failed: {e}", exc_info=True)
            return False

    def _resolve_olive_media_path(self, shot, stream_key: str, prefer_proxy_media: bool) -> Optional[Path]:
        """Pick the best source for Olive (prefer lightweight MP4 proxies when available)."""
        source_attr = f"{stream_key}_path"
        source_value = getattr(shot, source_attr, None)
        if not source_value:
            return None

        source_path = Path(source_value)
        if not prefer_proxy_media:
            return source_path

        # Already a video file: keep as-is.
        if source_path.suffix.lower() in self.video_extensions:
            return source_path

        # Explicit stream proxy path takes precedence.
        explicit_proxy = getattr(shot, f"{stream_key}_proxy_path", None)
        if explicit_proxy:
            explicit_proxy_path = Path(explicit_proxy)
            if explicit_proxy_path.exists() and explicit_proxy_path.suffix.lower() in self.video_extensions:
                return explicit_proxy_path

        # Generic proxy path fallback.
        generic_proxy = getattr(shot, "proxy_path", None)
        if generic_proxy:
            generic_proxy_path = Path(generic_proxy)
            if generic_proxy_path.exists() and generic_proxy_path.suffix.lower() in self.video_extensions:
                return generic_proxy_path

        # Last fallback: detect nearby MP4/MOV proxy candidates.
        nearby_proxy = self._find_nearby_proxy(source_path, getattr(shot, "name", ""), stream_key)
        return nearby_proxy or source_path

    def _find_nearby_proxy(self, media_path: Path, shot_name: str, stream_key: str) -> Optional[Path]:
        """Search sibling folders for likely proxy/review videos."""
        search_roots = []
        parent_dir = media_path.parent
        if parent_dir.exists():
            search_roots.append(parent_dir)

        for child_name in ("proxy", "proxies", "preview", "previews"):
            candidate_dir = parent_dir / child_name
            if candidate_dir.exists():
                search_roots.append(candidate_dir)

        candidates: List[Path] = []
        scanned = 0
        for root in search_roots:
            try:
                for file_path in root.iterdir():
                    scanned += 1
                    if scanned > self._max_proxy_scan_entries:
                        break
                    if file_path.is_file() and file_path.suffix.lower() in self.video_extensions:
                        candidates.append(file_path)
            except OSError:
                continue
            if scanned > self._max_proxy_scan_entries:
                logger.debug(
                    "Proxy scan capped at %s entries for %s",
                    self._max_proxy_scan_entries,
                    media_path,
                )
                break

        if not candidates:
            return None

        shot_token = (shot_name or "").lower()
        stream_token = stream_key.lower()

        def score(path: Path) -> tuple[int, float]:
            stem = path.stem.lower()
            points = 0
            if stream_token in stem:
                points += 4
            if "proxy" in stem:
                points += 3
            if shot_token and shot_token in stem:
                points += 2
            if "review" in stem or "preview" in stem:
                points += 1
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (points, mtime)

        return max(candidates, key=score)

    # --- Node Generators ---

    def _add_root_folder_node(self):
        """Creates the root 'Folder' node that holds everything"""
        node = ET.Element("node", version="1", id="org.olivevideoeditor.Olive.folder", ptr=self.root_ptr)
        ET.SubElement(node, "label").text = "Root"
        
        # Connections input (Where children live)
        ET.SubElement(node, "input", id="child_in")
        
        # Connect Sequence to Root
        self._add_connection(input_node_id="child_in", input_elem_index=0, output_ptr=self.sequence_ptr, parent_node_elem=node)
        
        self.nodes.append(node)

    def _add_sequence_node(self):
        """Creates the Timeline Sequence"""
        node = ET.Element("node", version="1", id="org.olivevideoeditor.Olive.sequence", ptr=self.sequence_ptr)
        ET.SubElement(node, "label").text = "VFX_Lineup"
        self.nodes.append(node)

    def _add_track_node(self, name: str) -> str:
        """Adds a Track node and connects it to the Sequence"""
        track_ptr = self._generate_ptr()
        node = ET.Element("node", version="1", id="org.olivevideoeditor.Olive.track", ptr=track_ptr)
        ET.SubElement(node, "label").text = name # Olive doesn't show track names but good for debug
        
        # Connect Track -> Sequence
        # We need to find the sequence node and add a connection TO it from this track
        # Actually in XML, the Parent (Sequence) lists the connection FROM the Child (Track)
        
        # We need to append this track to the Sequence's 'track_in_0' (or 1, 2)
        # For simplicity, we assume we just call this method in order V1, V2
        
        track_idx = len(self.tracks) # 0 for first track, 1 for second
        
        # Find Sequence Node
        seq_node = next(n for n in self.nodes if n.attrib['id'] == "org.olivevideoeditor.Olive.sequence")
        
        # Add connection on Sequence Node
        # <connection input="track_in_0" element="X"> <output>TRACK_PTR</output> </connection>
        
        # NOTE: Olive seems to use 'track_in_0' for V1? 
        # Sample showed track_in_0 having 2 elements. 
        # Wait, 'track_in_0' might be Video, 'track_in_1' Audio?
        # Let's assume 'track_in_0' is the main video stack.
        
        self._add_connection("track_in_0", track_idx, track_ptr, seq_node)
        
        self.tracks[track_ptr] = [] # Init clip list
        self.nodes.append(node)
        return track_ptr

    def _add_clip_to_track(
        self,
        track_ptr: str,
        file_path: Path,
        start_time: int,
        duration: int,
        fps: float,
        label: str,
    ):
        """
        Creates the Footage -> Transform -> Clip chain and connects to Track
        """
        # 1. Footage Node
        footage_ptr = self._generate_ptr()
        footage = ET.Element("node", version="1", id="org.olivevideoeditor.Olive.footage", ptr=footage_ptr)
        ET.SubElement(footage, "label").text = label
        
        # File Path Input
        file_in = ET.SubElement(footage, "input", id="file_in")
        primary = ET.SubElement(file_in, "primary")
        std = ET.SubElement(primary, "standard")
        ET.SubElement(std, "track").text = str(file_path).replace("\\", "/") # Olive likes forward slashes
        
        self.nodes.append(footage)
        
        # 2. Transform Node
        trans_ptr = self._generate_ptr()
        trans = ET.Element("node", version="1", id="org.olivevideoeditor.Olive.transform", ptr=trans_ptr)
        
        # Connect Footage -> Transform (tex_in)
        self._add_connection("tex_in", None, footage_ptr, trans)
        self.nodes.append(trans)
        
        # 3. Clip Node
        clip_ptr = self._generate_ptr()
        clip = ET.Element("node", version="1", id="org.olivevideoeditor.Olive.clip", ptr=clip_ptr)
        
        # Connect Transform -> Clip (buffer_in)
        self._add_connection("buffer_in", None, trans_ptr, clip)

        # Clip timing: without length/media offsets Olive may use defaults or zero-ish clips.
        self._set_clip_timing(clip, duration_frames=duration, start_frames=start_time, fps=fps)
        
        self.nodes.append(clip)
        
        # 4. Connect to Track
        track_node = next(n for n in self.nodes if n.attrib['ptr'] == track_ptr)
        
        # Appending to 'block_in'
        # We need to know how many clips are already on this track
        clip_idx = len(self.tracks[track_ptr])
        
        self._add_connection("block_in", clip_idx, clip_ptr, track_node)
        
        self.tracks[track_ptr].append(clip_ptr)

    def _set_clip_timing(self, clip_elem: ET.Element, duration_frames: int, start_frames: int, fps: float) -> None:
        """Attach timing inputs to clip node using rational seconds format accepted by Olive."""
        safe_fps = float(fps) if fps and fps > 0 else 24.0
        length_val = self._seconds_fraction(duration_frames, safe_fps)
        media_start_val = self._seconds_fraction(0, safe_fps)
        timeline_start_val = self._seconds_fraction(max(0, start_frames), safe_fps)

        self._set_track_input_value(clip_elem, "length_in", length_val)
        self._set_track_input_value(clip_elem, "media_in", media_start_val)
        self._set_track_input_value(clip_elem, "timeline_in", timeline_start_val)

    def _seconds_fraction(self, frames: int, fps: float) -> str:
        """Convert frame count to reduced rational seconds string (e.g. 1001/24000)."""
        frame_count = max(0, int(frames))
        fps_value = float(fps) if fps and fps > 0 else 24.0

        # Use millisecond precision rational to stay deterministic and avoid float drift.
        numerator = frame_count * 1000
        denominator = max(1, int(round(fps_value * 1000)))
        gcd = math.gcd(numerator, denominator)
        return f"{numerator // gcd}/{denominator // gcd}"

    def _set_track_input_value(self, node_elem: ET.Element, input_id: str, value: str) -> None:
        input_elem = ET.SubElement(node_elem, "input", id=input_id)
        primary = ET.SubElement(input_elem, "primary")
        standard = ET.SubElement(primary, "standard")
        ET.SubElement(standard, "track").text = value

    def _add_connection(self, input_node_id, input_elem_index: Optional[int], output_ptr, parent_node_elem):
        """Adds a <connection> tag to the parent node"""
        conns = parent_node_elem.find("connections")
        if conns is None:
            conns = ET.SubElement(parent_node_elem, "connections")
            
        conn = ET.SubElement(conns, "connection")
        conn.set("input", input_node_id)
        if input_elem_index is not None:
            conn.set("element", str(input_elem_index))
            
        ET.SubElement(conn, "output").text = output_ptr

    def _add_settings(self, project_elem):
        settings = ET.SubElement(project_elem, "settings")
        ET.SubElement(settings, "root").text = self.root_ptr

    def _add_layout(self, project_root):
        # Minimal layout to make Olive happy
        layout = ET.SubElement(project_root, "layout", version="1")
        timeline = ET.SubElement(layout, "timeline")
        ET.SubElement(timeline, "sequence").text = self.sequence_ptr
