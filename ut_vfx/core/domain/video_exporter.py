"""
Video Export Manager

Exports timeline to MP4 with FFmpeg.
Supports side-by-side, over/under layouts and burnins.
"""

from pathlib import Path
from typing import List, Dict, Optional
import subprocess
import logging

from .review_shot import ReviewShot
from ...utils.resource_manager import ResourcePathManager
from ...utils.media_capabilities import is_video

logger = logging.getLogger(__name__)


class VideoExporter:
    """
    Export review lineup to video files
    
    Uses FFmpeg for professional encoding with burnins.
    """
    
    PRESETS = {
        'client_review': {
            'format': 'mp4',
            'codec': 'libx264',
            'resolution': (1920, 1080),
            'bitrate': '10M',
            'quality': 'high'
        },
        'high_quality': {
            'format': 'mov',
            'codec': 'prores',
            'resolution': 'source',
            'bitrate': None,
            'quality': 'highest'
        },
        'web_preview': {
            'format': 'mp4',
            'codec': 'libx264',
            'resolution': (1280, 720),
            'bitrate': '5M',
            'quality': 'medium'
        }
    }
    
    def __init__(self, config_manager=None):
        self.ffmpeg_path = ResourcePathManager.get_ffmpeg_path()
        self.config = config_manager
        self.presets = {k: v.copy() for k, v in self.PRESETS.items()}
        
        # Merge configured presets if available
        if self.config:
            try:
                configured = self.config.settings.get("export_presets", {})
                if isinstance(configured, dict):
                    # Shallow merge configured values over defaults.
                    self.presets.update(configured)
            except Exception as e:
                logger.warning(f"Could not load export presets from config: {e}")

    def export_comparison_video(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str = 'client_review',
        layout: str = "Side-by-Side",
        burnin_config: Optional[Dict] = None,
    ) -> bool:
        """Main export entry used by the export dialog."""
        layout_key = (layout or "").strip().lower()

        if layout_key == "side-by-side":
            return self.export_side_by_side(shots, output_path, preset, burnin_config)
        if layout_key == "over/under":
            return self.export_over_under(shots, output_path, preset, burnin_config)
        if layout_key == "render only":
            return self.export_render_only(shots, output_path, preset, burnin_config)
        if layout_key == "scan only":
            return self.export_scan_only(shots, output_path, preset, burnin_config)

        logger.warning("Unknown layout '%s', falling back to Side-by-Side", layout)
        return self.export_side_by_side(shots, output_path, preset, burnin_config)

    def export_side_by_side(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str = 'client_review',
        burnin_config: Optional[Dict] = None
    ) -> bool:
        """Export with horizontal stacking: scan left, render right."""
        return self._export_dual_input(
            shots=shots,
            output_path=output_path,
            preset=preset,
            burnin_config=burnin_config,
            stack_filter="hstack",
        )

    def export_over_under(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str = 'client_review',
        burnin_config: Optional[Dict] = None
    ) -> bool:
        """Export with vertical stacking: scan top, render bottom."""
        return self._export_dual_input(
            shots=shots,
            output_path=output_path,
            preset=preset,
            burnin_config=burnin_config,
            stack_filter="vstack",
        )

    def export_render_only(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str = 'client_review',
        burnin_config: Optional[Dict] = None
    ) -> bool:
        """Export render stream only."""
        return self._export_single_source(
            shots=shots,
            output_path=output_path,
            preset=preset,
            burnin_config=burnin_config,
            source_key="render",
        )

    def export_scan_only(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str = 'client_review',
        burnin_config: Optional[Dict] = None
    ) -> bool:
        """Export scan stream only."""
        return self._export_single_source(
            shots=shots,
            output_path=output_path,
            preset=preset,
            burnin_config=burnin_config,
            source_key="scan",
        )

    def _export_dual_input(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str,
        burnin_config: Optional[Dict],
        stack_filter: str,
    ) -> bool:
        try:
            logger.info("Exporting dual-input (%s) to %s", stack_filter, output_path)
            
            if not self.check_ffmpeg_available():
                logger.error("FFmpeg not found")
                return False
                
            # Case 1: Single Shot
            if len(shots) == 1:
                return self._export_single_shot_dual(
                    shot=shots[0],
                    output_path=output_path,
                    preset=preset,
                    burnin_config=burnin_config,
                    stack_filter=stack_filter,
                )
            
            # Case 2: Multi-shot sequence: render temp clips then concat.
            import tempfile
            
            with tempfile.TemporaryDirectory(prefix="ut_vfx_export_") as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                temp_clips = []
                
                for i, shot in enumerate(shots):
                    safe_name = "".join([c for c in shot.name if c.isalnum() or c in (' ', '-', '_')]).strip()
                    temp_out = temp_dir / f"clip_{i:03d}_{safe_name}.mp4"
                    
                    if self._export_single_shot_dual(
                        shot=shot,
                        output_path=temp_out,
                        preset=preset,
                        burnin_config=burnin_config,
                        stack_filter=stack_filter,
                    ):
                        temp_clips.append(temp_out)
                    else:
                        logger.warning(f"Failed to render shot {shot.name}, skipping")
                
                if not temp_clips:
                    logger.error("No clips rendered successfully")
                    return False
                return self._concat_temp_clips(temp_clips, output_path, temp_dir)
        
        except Exception as e:
            logger.error(f"Export error: {e}", exc_info=True)
            return False

    def _export_single_source(
        self,
        shots: List[ReviewShot],
        output_path: Path,
        preset: str,
        burnin_config: Optional[Dict],
        source_key: str,
    ) -> bool:
        try:
            logger.info("Exporting %s-only to %s", source_key, output_path)
            if not self.check_ffmpeg_available():
                logger.error("FFmpeg not found")
                return False

            if len(shots) == 1:
                return self._export_single_shot_source(
                    shot=shots[0],
                    output_path=output_path,
                    preset=preset,
                    burnin_config=burnin_config,
                    source_key=source_key,
                )

            import tempfile
            with tempfile.TemporaryDirectory(prefix="ut_vfx_export_") as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                temp_clips = []

                for i, shot in enumerate(shots):
                    safe_name = "".join([c for c in shot.name if c.isalnum() or c in (' ', '-', '_')]).strip()
                    temp_out = temp_dir / f"clip_{i:03d}_{safe_name}.mp4"
                    if self._export_single_shot_source(
                        shot=shot,
                        output_path=temp_out,
                        preset=preset,
                        burnin_config=burnin_config,
                        source_key=source_key,
                    ):
                        temp_clips.append(temp_out)
                    else:
                        logger.warning("Failed to render %s for shot %s, skipping", source_key, shot.name)

                if not temp_clips:
                    logger.error("No clips rendered successfully")
                    return False
                return self._concat_temp_clips(temp_clips, output_path, temp_dir)

        except Exception as e:
            logger.error(f"Export error: {e}", exc_info=True)
            return False

    def _concat_temp_clips(self, temp_clips: List[Path], output_path: Path, temp_dir: Path) -> bool:
        """Concat already-rendered clips into final output."""
        list_path = temp_dir / "concat_list.txt"
        with open(list_path, 'w', encoding='utf-8') as f:
            for clip in temp_clips:
                p = str(clip.absolute()).replace('\\', '/')
                f.write(f"file '{p}'\n")

        cmd = [self.ffmpeg_path, '-f', 'concat', '-safe', '0', '-i', str(list_path), '-c', 'copy', '-y', str(output_path)]
        logger.info(f"Concat command: {' '.join(cmd)}")

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error(f"Concat failed: {res.stderr}")
            return False
        return True

    def _export_single_shot_sbs(self, shot: ReviewShot, output_path: Path, preset: str, burnin_config: Optional[Dict]) -> bool:
        """Backward-compatible wrapper for side-by-side single-shot export."""
        return self._export_single_shot_dual(
            shot=shot,
            output_path=output_path,
            preset=preset,
            burnin_config=burnin_config,
            stack_filter="hstack",
        )

    def _export_single_shot_dual(
        self,
        shot: ReviewShot,
        output_path: Path,
        preset: str,
        burnin_config: Optional[Dict],
        stack_filter: str,
    ) -> bool:
        """Export single shot with dual inputs stacked by stack_filter (hstack/vstack)."""
        if not shot.is_complete():
            logger.error(f"Shot {shot.name} is incomplete (missing scan or render)")
            return False
            
        # Get start frames
        scan_path = shot.scan_path
        render_path = shot.render_path
        
        if not scan_path or not render_path:
            return False

        if not shot.frame_range:
            logger.error("Shot %s has no frame range", shot.name)
            return False
            
        start_number = shot.frame_range[0]
        
        # Build command
        cmd = [self.ffmpeg_path]
        
        self._append_media_input(cmd, scan_path, start_number)
        self._append_media_input(cmd, render_path, start_number)
        
        filter_str = f"[0:v][1:v]{stack_filter}=inputs=2[base]"
        
        if burnin_config:
            burnins = self._build_burnin_filters(burnin_config)
            if burnins:
                filter_str += f";[base]{burnins}[out]"
            else:
                filter_str += ";[base]null[out]"
        else:
            filter_str += ";[base]null[out]"
             
        cmd.extend(['-filter_complex', filter_str, '-map', '[out]'])
        self._apply_encoding_settings(cmd, preset)
        cmd.extend(['-y', str(output_path)])
        
        logger.info(f"Running FFmpeg: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg failed: {result.stderr}")
            return False
            
        return True

    def _export_single_shot_source(
        self,
        shot: ReviewShot,
        output_path: Path,
        preset: str,
        burnin_config: Optional[Dict],
        source_key: str,
    ) -> bool:
        """Export single shot from one source stream."""
        media_path = shot.render_path if source_key == "render" else shot.scan_path
        if not media_path:
            logger.error("Shot %s missing %s path", shot.name, source_key)
            return False

        if not shot.frame_range:
            logger.error("Shot %s has no frame range", shot.name)
            return False

        start_number = shot.frame_range[0]
        cmd = [self.ffmpeg_path]
        self._append_media_input(cmd, media_path, start_number)

        if burnin_config:
            burnins = self._build_burnin_filters(burnin_config)
            if burnins:
                cmd.extend(['-vf', burnins])

        self._apply_encoding_settings(cmd, preset)
        cmd.extend(['-y', str(output_path)])

        logger.info(f"Running FFmpeg: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"FFmpeg failed: {result.stderr}")
            return False
        return True

    def _append_media_input(self, cmd: List[str], media_path: Path, start_number: int) -> None:
        """Append ffmpeg input args for a media path (movie or image sequence)."""
        suffix = media_path.suffix.lower()
        if is_video(suffix):
            cmd.extend(['-i', str(media_path)])
            return

        input_pattern = self._get_ffmpeg_input_pattern(media_path, start_number)
        cmd.extend(['-start_number', str(start_number), '-i', str(input_pattern)])

    def _apply_encoding_settings(self, cmd: List[str], preset: str) -> None:
        """Append codec/bitrate/resolution options from preset."""
        config = self.presets.get(preset, self.presets['client_review'])
        cmd.extend(['-c:v', config['codec']])

        if config.get('bitrate'):
            cmd.extend(['-b:v', config['bitrate']])

        resolution = config.get('resolution')
        if resolution != 'source' and isinstance(resolution, tuple):
            w, h = resolution
            cmd.extend(['-s', f'{w}x{h}'])

    def _get_ffmpeg_input_pattern(self, full_path: Path, start_frame: int) -> str:
        """Convert a specific frame path to ffmpeg pattern (path.%04d.exr)"""
        # Get the folder and filename
        folder = full_path.parent
        filename = full_path.name
        
        import re

        # Already an ffmpeg-style pattern.
        if "%" in filename and "d" in filename:
            return str(full_path)

        # Hash-style pattern: shot.####.exr -> shot.%04d.exr
        hash_match = re.search(r"(#+)", filename)
        if hash_match:
            pad_len = len(hash_match.group(1))
            return str(folder / re.sub(r"(#+)", f"%0{pad_len}d", filename, count=1))

        # Preserve existing frame padding from filename when possible.
        frame_match = re.search(r"(\d+)(?=\.[^.]+$)", filename)
        if frame_match:
            digits = frame_match.group(1)
            pad_len = max(1, len(digits))
            start_idx, end_idx = frame_match.span(1)
            prefix = filename[:start_idx]
            suffix = filename[end_idx:]
            return str(folder / f"{prefix}%0{pad_len}d{suffix}")

        frame_str = str(start_frame)
        if frame_str in filename:
            parts = filename.split(frame_str, 1)
            if len(parts) > 1:
                pad_len = max(1, len(frame_str))
                return str(folder / f"{parts[0]}%0{pad_len}d{parts[-1]}")
        
        # Fallback: treat as single-file input path.
        return str(full_path)

    def _create_concat_file(self, shots: List[ReviewShot], layout: str) -> Path:
        """Internal helper for legacy concat workflows."""
        import tempfile

        layout_key = (layout or "").strip().lower()
        concat_fd = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="ut_vfx_concat_",
            delete=False,
        )

        with concat_fd as handle:
            for shot in shots:
                source_path = None
                if "render" in layout_key:
                    source_path = shot.render_path
                elif "scan" in layout_key:
                    source_path = shot.scan_path
                else:
                    source_path = shot.render_path or shot.scan_path

                if not source_path:
                    continue

                src = Path(source_path).resolve()
                safe_path = str(src).replace("\\", "/")
                handle.write(f"file '{safe_path}'\n")

        return Path(concat_fd.name)
    
    def _build_ffmpeg_command(
        self,
        input_file: Path,
        output_path: Path,
        preset: str,
        layout: str,
        burnin_config: Optional[Dict] = None
    ) -> List[str]:
        """Build FFmpeg command with all options"""
        
        config = self.presets.get(preset, self.presets['client_review'])
        
        cmd = [self.ffmpeg_path]
        
        # Input
        cmd.extend(['-f', 'concat', '-safe', '0', '-i', str(input_file)])
        
        # Video codec
        cmd.extend(['-c:v', config['codec']])
        
        # Bitrate (if specified)
        if config['bitrate']:
            cmd.extend(['-b:v', config['bitrate']])
        
        # Resolution
        if config['resolution'] != 'source':
            w, h = config['resolution']
            cmd.extend(['-s', f'{w}x{h}'])
        
        # Quality
        if config['codec'] == 'libx264':
            crf = {'high': 18, 'medium': 23, 'low': 28}.get(config['quality'], 23)
            cmd.extend(['-crf', str(crf)])
        
        # Burnin filters
        if burnin_config:
            filters = self._build_burnin_filters(burnin_config)
            if filters:
                cmd.extend(['-vf', filters])
        
        # Output
        cmd.append(str(output_path))
        
        return cmd
    
    def _build_burnin_filters(self, config: Dict) -> str:
        """Build FFmpeg drawtext filters for burnin"""
        filters = []
        
        # Shot name
        if config.get('shot_name'):
            filters.append(
                "drawtext=text='Shot\\: %{metadata\\:shot_name}'"
                ":x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"
            )
        
        # Frame counter
        if config.get('frame_counter'):
            filters.append(
                "drawtext=text='Frame\\: %{frame_num}'"
                ":x=10:y=40:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5"
            )
        
        # Timecode
        if config.get('timecode'):
            filters.append(
                "drawtext=text='TC\\: %{pts\\:hms}'"
                ":x=10:y=70:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5"
            )
        
        # Status watermark
        if config.get('status'):
            filters.append(
                "drawtext=text='APPROVED'"
                ":x=(w-text_w)/2:y=h-30:fontsize=48:fontcolor=green:alpha=0.5"
            )
        
        return ','.join(filters) if filters else ''
    
    def check_ffmpeg_available(self) -> bool:
        """Check if FFmpeg is available"""
        try:
            result = subprocess.run(
                [self.ffmpeg_path, '-version'],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
