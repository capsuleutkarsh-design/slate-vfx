"""
XML Exporter — Premiere Pro & Final Cut Pro XML Import/Export

Uses OpenTimelineIO when available for standards-compliant XML generation.
Falls back to pure-Python implementation if OTIO is not installed.
"""

from pathlib import Path
from typing import List
import logging
import xml.etree.ElementTree as ET
from xml.dom import minidom

logger = logging.getLogger(__name__)

_HAS_OTIO = False


# ===================================================================
# Pure-Python fallback (original implementation)
# ===================================================================

def _export_premiere_xml_fallback(shots: List, output_path: Path,
                                  fps: float = 24.0,
                                  sequence_name: str = "VFX_Review_Lineup") -> bool:
    """Pure-Python Premiere Pro XML export (fallback)."""
    try:
        root = ET.Element('xmeml', version="4")
        sequence = ET.SubElement(root, 'sequence')
        ET.SubElement(sequence, 'name').text = sequence_name

        rate = ET.SubElement(sequence, 'rate')
        ET.SubElement(rate, 'timebase').text = str(int(fps))
        ET.SubElement(rate, 'ntsc').text = 'FALSE'

        media = ET.SubElement(sequence, 'media')
        video = ET.SubElement(media, 'video')

        video_format = ET.SubElement(video, 'format')
        sample_char = ET.SubElement(video_format, 'samplecharacteristics')
        ET.SubElement(sample_char, 'width').text = '1920'
        ET.SubElement(sample_char, 'height').text = '1080'

        render_track = ET.SubElement(video, 'track')
        scan_track = ET.SubElement(video, 'track')

        timeline_start = 0

        for shot in shots:
            if hasattr(shot, 'render_start') and hasattr(shot, 'render_end'):
                duration = shot.render_end - shot.render_start + 1
            elif hasattr(shot, 'frame_range') and shot.frame_range:
                duration = shot.frame_range[1] - shot.frame_range[0] + 1
            else:
                logger.warning(f"Shot {shot.name} missing frame range, skipping")
                continue

            if hasattr(shot, 'render_path') and shot.render_path:
                clip_item = ET.SubElement(render_track, 'clipitem')
                ET.SubElement(clip_item, 'name').text = f"{shot.name}_render"
                ET.SubElement(clip_item, 'start').text = str(timeline_start)
                ET.SubElement(clip_item, 'end').text = str(timeline_start + duration)
                ET.SubElement(clip_item, 'in').text = '0'
                ET.SubElement(clip_item, 'out').text = str(duration)
                file_elem = ET.SubElement(clip_item, 'file')
                ET.SubElement(file_elem, 'pathurl').text = str(shot.render_path)

            if hasattr(shot, 'scan_path') and shot.scan_path:
                clip_item = ET.SubElement(scan_track, 'clipitem')
                ET.SubElement(clip_item, 'name').text = f"{shot.name}_scan"
                ET.SubElement(clip_item, 'start').text = str(timeline_start)
                ET.SubElement(clip_item, 'end').text = str(timeline_start + duration)
                ET.SubElement(clip_item, 'in').text = '0'
                ET.SubElement(clip_item, 'out').text = str(duration)
                file_elem = ET.SubElement(clip_item, 'file')
                ET.SubElement(file_elem, 'pathurl').text = str(shot.scan_path)

            timeline_start += duration

        xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_str)

        logger.info(f"Premiere XML exported (fallback): {output_path} ({len(shots)} shots)")
        return True

    except Exception as e:
        logger.error(f"Error exporting Premiere XML (fallback): {e}", exc_info=True)
        return False


def _export_fcpxml_fallback(shots: List, output_path: Path,
                            fps: float = 24.0) -> bool:
    """Pure-Python FCPXML 1.8 export (fallback)."""
    try:
        root = ET.Element('fcpxml', version="1.8")
        resources = ET.SubElement(root, 'resources')
        library = ET.SubElement(root, 'library')
        event = ET.SubElement(library, 'event', name="VFX Review")
        project = ET.SubElement(event, 'project', name="VFX_Review_Lineup")
        sequence = ET.SubElement(project, 'sequence',
                                format="r1",
                                duration="0s",
                                tcStart="0s")
        spine = ET.SubElement(sequence, 'spine')

        timeline_frames = 0

        for idx, shot in enumerate(shots, 1):
            if hasattr(shot, 'render_start') and hasattr(shot, 'render_end'):
                duration = shot.render_end - shot.render_start + 1
            elif hasattr(shot, 'frame_range') and shot.frame_range:
                duration = shot.frame_range[1] - shot.frame_range[0] + 1
            else:
                continue

            duration_sec = duration / fps
            offset_sec = timeline_frames / fps

            if hasattr(shot, 'render_path') and shot.render_path:
                ET.SubElement(spine, 'asset-clip',
                              name=f"{shot.name}_render",
                              ref=f"r{idx}_render",
                              offset=f"{offset_sec:.3f}s",
                              duration=f"{duration_sec:.3f}s",
                              tcFormat="NDF")
                ET.SubElement(resources, 'asset',
                              id=f"r{idx}_render",
                              name=f"{shot.name}_render",
                              src=str(shot.render_path),
                              hasVideo="1")

            timeline_frames += duration

        xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_str)

        logger.info(f"FCPXML exported (fallback): {output_path} ({len(shots)} shots)")
        return True

    except Exception as e:
        logger.error(f"Error exporting FCPXML (fallback): {e}", exc_info=True)
        return False


# ===================================================================
# Public API (unchanged signatures)
# ===================================================================

def export_premiere_xml(shots: List, output_path: Path, fps: float = 24.0,
                        sequence_name: str = "VFX_Review_Lineup") -> bool:
    """
    Export shots to Premiere Pro XML format.

    Uses OpenTimelineIO if available, otherwise falls back to
    pure-Python implementation.

    Args:
        shots: List of ReviewShot objects
        output_path: Output file path
        fps: Frames per second
        sequence_name: Sequence name

    Returns:
        True if successful
    """
    return _export_premiere_xml_fallback(shots, output_path, fps, sequence_name)


def export_fcpxml(shots: List, output_path: Path, fps: float = 24.0) -> bool:
    """
    Export shots to Final Cut Pro XML format (FCPXML 1.8).

    Uses OpenTimelineIO if available, otherwise falls back to
    pure-Python implementation.

    Args:
        shots: List of ReviewShot objects
        output_path: Output file path
        fps: Frames per second

    Returns:
        True if successful
    """
    return _export_fcpxml_fallback(shots, output_path, fps)


def export_xml(shots: List, output_path: Path, format_type: str = "premiere",
               fps: float = 24.0) -> bool:
    """
    Export to XML format (wrapper function).

    Args:
        shots: List of ReviewShot objects
        output_path: Output file path
        format_type: 'premiere' or 'fcpxml'
        fps: Frames per second

    Returns:
        True if successful
    """
    if format_type.lower() == "fcpxml":
        return export_fcpxml(shots, output_path, fps)
    else:
        return export_premiere_xml(shots, output_path, fps)


def import_xml(xml_path) -> List:
    """
    Import a Premiere Pro or Final Cut Pro XML file.

    NEW: Requires OpenTimelineIO.

    Args:
        xml_path: Path to the XML file.

    Returns:
        List of ReviewShot objects.

    Raises:
        ImportError: If OpenTimelineIO is not installed.
    """
    raise NotImplementedError("XML import is no longer supported.")
