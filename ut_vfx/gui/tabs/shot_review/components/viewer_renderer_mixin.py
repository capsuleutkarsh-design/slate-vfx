import logging
from PySide6.QtCore import Qt, QRect, QPoint, QSize
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QPen, QBrush
import cv2
import numpy as np
from ..comparison_viewer import ViewMode

class ViewerRendererMixin:

    def update_display(self):
            """Apply grading and update viewers"""
            scan_disp = self.apply_grading(self.scan_image)
            render_disp = self.apply_grading(self.render_image)

            # --- SINGLE VIEW (PREMIERE style) ---
            if self.single_view_mode:
                # Logic: Top Track (Render/V2) covers Bottom Track (Scan/V1)
                final_img = None
                source_label = "EMPTY"

                # Check V2 (Render) first
                if self.show_render_track and render_disp is not None:
                    final_img = render_disp
                    source_label = "V2: RENDER"
                # Fallback to V1 (Scan)
                elif self.show_scan_track and scan_disp is not None:
                    final_img = scan_disp
                    source_label = "V1: SCAN"

                self.render_viewer.load_image(final_img, None)
                self.render_viewer.set_overlay_text(f"{source_label}")
                return

            # --- SCAN ONLY MODE ---
            if self.current_view_mode == ViewMode.SCAN_ONLY:
                if scan_disp is not None:
                    self.scan_viewer.load_image(scan_disp, None)
                return

            # --- RENDER ONLY MODE ---
            if self.current_view_mode == ViewMode.RENDER_ONLY:
                if render_disp is not None:
                    self.render_viewer.load_image(render_disp, None)
                    self.render_viewer.set_title("RENDER (Comp)")
                return

            if self.current_view_mode == ViewMode.WIPE:
                if scan_disp is not None and render_disp is not None:
                    # Resize render to match scan if needed (simple crop/pad or just skip mismatch)
                    if scan_disp.shape == render_disp.shape:
                        h, w, c = scan_disp.shape
                        split = int(w * self.wipe_position)
                        split = max(0, min(split, w))

                        combined = scan_disp.copy()
                        # Left: Scan, Right: Render
                        combined[:, split:] = render_disp[:, split:]

                        # Draw wipe line
                        cv2.line(combined, (split, 0), (split, h), (255, 255, 0), 2)

                        self.scan_viewer.load_image(combined, None)
                        return

            elif self.current_view_mode == ViewMode.DIFFERENCE:
                if scan_disp is not None and render_disp is not None:
                    if scan_disp.shape == render_disp.shape:
                        # Calculate difference
                        diff = cv2.absdiff(scan_disp, render_disp)
                        # Amplify visibility
                        diff = cv2.multiply(diff, np.array([5.0])) 
                        self.scan_viewer.load_image(diff, None)
                        return

            # Standard side-by-side mode
            if self.scan_image is not None:
                self.scan_viewer.load_image(scan_disp, None)

            if self.render_image is not None:
                self.render_viewer.load_image(render_disp, None)

    def apply_grading(self, image, allow_downscale=False):
            """Apply color grading to image (numpy array)"""
            if image is None: return None

            # Skip processing if default values AND not downscaling
            if self.gain == 1.0 and self.gamma == 1.0 and self.saturation == 1.0 and not allow_downscale:
                # Always return full resolution if no grading applied
                return image

            # Optimization: Downscale ONLY during active slider dragging
            process_img = image
            is_downscaled = False

            if allow_downscale:
                # 50%  scale for speed during slider drag
                h, w = image.shape[:2]
                process_img = cv2.resize(image, (w//2, h//2), interpolation=cv2.INTER_LINEAR)
                is_downscaled = True

            try:
                # Fast Look-Up Table (LUT) Approach
                # Check cache
                current_values = (self.gain, self.gamma)

                # Initialize cache if missing
                if not hasattr(self, '_lut_cache'):
                    self._lut_cache = None
                if not hasattr(self, '_lut_cache_values'):
                    self._lut_cache_values = None

                # Regenerate if needed
                if self._lut_cache is None or self._lut_cache_values != current_values:
                    x = np.arange(256).astype(np.float32) / 255.0

                    # Gain
                    if self.gain != 1.0:
                        x = x * self.gain

                    # Gamma
                    if self.gamma != 1.0 and self.gamma > 0:
                        x = np.power(np.maximum(x, 0), 1.0 / self.gamma)

                    # Clip
                    x = np.clip(x, 0, 1) * 255.0
                    self._lut_cache = x.astype(np.uint8)
                    self._lut_cache_values = current_values

                # Apply LUT for Gain/Gamma (very fast)
                graded = cv2.LUT(process_img, self._lut_cache)

                # Saturation
                if self.saturation != 1.0:
                    # Optimized Saturation using weighted add
                    gray = cv2.cvtColor(graded, cv2.COLOR_RGB2GRAY)
                    gray_3c = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)

                    # alpha blend: graded * sat + gray * (1-sat)
                    graded = cv2.addWeighted(graded, self.saturation, gray_3c, 1.0 - self.saturation, 0)

                # If we downscaled for performance during interactive grading,
                # scale back up to full resolution for final display
                if is_downscaled:
                    h, w = image.shape[:2]
                    graded = cv2.resize(graded, (w, h), interpolation=cv2.INTER_LINEAR)

                return graded

            except Exception as e:
                logger.error(f"Grading error: {e}")
                return image

    def update_hud(self):
            """Update HUD overlay text on viewers"""
            if not self.is_hud_enabled:
                self.scan_viewer.set_overlay_text("")
                self.render_viewer.set_overlay_text("")
                return

            import re  # For version extraction

            # Scan Info
            scan_txt = "SCAN"
            if self.current_shot and self.current_shot.scan_path:
                full_path = str(self.current_shot.scan_path)

                # Try to extract version number (v001, v002, etc.)
                version_match = re.search(r'[vV](\d+)', full_path)
                if version_match:
                    version_num = version_match.group(0).upper()  # e.g., "V001"
                    scan_txt += f" [{version_num}]"

                # Also try to show scan category (Scan, 01_Scan, etc.)
                scan_type_match = re.search(r'(\d+_)?[Ss]can', full_path)
                if scan_type_match:
                    scan_txt += " SCAN"

                # Get actual image size if loaded
                w, h = self.scan_viewer.get_image_size()
                if w > 0:
                    scan_txt += f" | {w}x{h}"
                scan_txt += f" | {self.current_shot.format}"
                scan_txt += f" | Frame {self.current_frame}"
            self.scan_viewer.set_overlay_text(scan_txt)

            # Render Info
            render_txt = "RENDER"
            if self.current_shot and self.current_shot.render_path:
                full_path = str(self.current_shot.render_path)

                # Try to extract version number (v001, v002, v003, etc.)
                version_match = re.search(r'[vV](\d+)', full_path)
                if version_match:
                    version_num = version_match.group(0).upper()  # e.g., "V001"
                    render_txt += f" [{version_num}]"

                # Extract render type from filename pattern (e.g., SS1_101_0040_PREP_v001)
                # Try filename pattern first: {shot}_{type}_{version}
                filename_match = re.search(r'_(PREP|COMP|PRECOMP|OUTPUT|BG|FG|FINAL)_[vV]\d+', full_path, re.IGNORECASE)
                if filename_match:
                    render_type = filename_match.group(1).upper()
                    render_txt += f" {render_type}"
                else:
                    # Fallback: search anywhere in path for render type
                    render_type_match = re.search(r'(PREP|COMP|OUTPUT|PRECOMP|BG|FG|FINAL)', full_path, re.IGNORECASE)
                    if render_type_match:
                        render_type = render_type_match.group(0).upper()
                        render_txt += f" {render_type}"

                w, h = self.render_viewer.get_image_size()
                if w > 0:
                    render_txt += f" | {w}x{h}"
                render_txt += f" | {self.current_shot.format}"
                render_txt += f" | Frame {self.current_frame}"
            self.render_viewer.set_overlay_text(render_txt)
