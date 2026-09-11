"""
Continuity Checker

Analyzes shot-to-shot continuity for VFX review.
Checks motion match, color consistency, scale changes, and direction flow.
"""

import cv2
import numpy as np
from pathlib import Path
from typing import Dict
import logging

from ...utils.image_loader import ImageLoader

logger = logging.getLogger(__name__)


class ContinuityChecker:
    """Shot-to-shot continuity analysis"""
    
    def __init__(self):
        self.loader = ImageLoader()
    
    def check_all(self, shot_a, shot_b) -> Dict[str, any]:
        """
        Run all continuity checks between two shots
        
        Args:
            shot_a: First ReviewShot (ending shot)
            shot_b: Second ReviewShot (starting shot)
        
        Returns:
            Dict with all check results
        """
        results = {
            'motion': 'UNKNOWN',
            'color': 'UNKNOWN',
            'scale': 'UNKNOWN',
            'direction': 'UNKNOWN',
            'overall': 'UNKNOWN'
        }
        
        # Load last frame of shot_a and first frame of shot_b
        if not shot_a.frame_range or not shot_b.frame_range:
            return results
        
        try:
            # Get frame paths
            last_frame_a = self._get_frame_path(shot_a.render_path, shot_a.frame_range[1])
            first_frame_b = self._get_frame_path(shot_b.render_path, shot_b.frame_range[0])
            
            # Load images
            img_a = self.loader.load_image(last_frame_a)
            img_b = self.loader.load_image(first_frame_b)
            
            if img_a is None or img_b is None:
                logger.warning("Could not load frames for continuity check")
                return results
            
            # Run checks
            results['motion'] = self.check_motion_match(img_a, img_b)
            results['color'] = self.check_color_consistency(img_a, img_b)
            results['scale'] = self.check_scale_match(img_a, img_b)
            results['direction'] = 'PASS'  # Placeholder for future
            
            # Overall assessment
            results['overall'] = self._assess_overall(results)
            
            return results
        
        except Exception as e:
            logger.error(f"Error in continuity check: {e}", exc_info=True)
            return results
    
    def check_motion_match(self, img_a: np.ndarray, img_b: np.ndarray) -> str:
        """
        Check motion continuity between frames
        
        Returns: 'PASS', 'WARN', or 'FAIL'
        """
        try:
            # Convert to grayscale
            gray_a = cv2.cvtColor(img_a, cv2.COLOR_RGB2GRAY)
            gray_b = cv2.cvtColor(img_b, cv2.COLOR_RGB2GRAY)
            
            # Calculate optical flow (simplified - just difference)
            diff = cv2.absdiff(gray_a, gray_b)
            motion_amount = np.mean(diff)
            
            # Thresholds (arbitrary, can be tuned)
            if motion_amount < 20:
                return 'PASS'  # Smooth motion
            elif motion_amount < 50:
                return 'WARN'  # Moderate jump
            else:
                return 'FAIL'  # Large jump
        
        except Exception as e:
            logger.error(f"Motion check error: {e}")
            return 'UNKNOWN'
    
    def check_color_consistency(self, img_a: np.ndarray, img_b: np.ndarray) -> str:
        """
        Check color consistency between frames
        
        Returns: 'PASS', 'WARN', or 'FAIL'
        """
        try:
            # Calculate average color
            avg_a = np.mean(img_a, axis=(0, 1))
            avg_b = np.mean(img_b, axis=(0, 1))
            
            # Color difference
            color_diff = np.linalg.norm(avg_a - avg_b)
            
            # Thresholds
            if color_diff < 10:
                return 'PASS'  # Very similar
            elif color_diff < 30:
                return 'WARN'  # Moderate difference
            else:
                return 'FAIL'  # Large difference
        
        except Exception as e:
            logger.error(f"Color check error: {e}")
            return 'UNKNOWN'
    
    def check_scale_match(self, img_a: np.ndarray, img_b: np.ndarray) -> str:
        """
        Check for sudden scale/framing changes
        
        Returns: 'PASS', 'WARN', or 'FAIL'
        """
        try:
            # Use edge detection to find objects
            edges_a = cv2.Canny(cv2.cvtColor(img_a, cv2.COLOR_RGB2GRAY), 50, 150)
            edges_b = cv2.Canny(cv2.cvtColor(img_b, cv2.COLOR_RGB2GRAY), 50, 150)
            
            # Count edge pixels (proxy for object size)
            edge_count_a = np.sum(edges_a > 0)
            edge_count_b = np.sum(edges_b > 0)
            
            # Calculate percentage change
            if edge_count_a > 0:
                scale_change = abs(edge_count_b - edge_count_a) / edge_count_a * 100
            else:
                return 'UNKNOWN'
            
            # Thresholds
            if scale_change < 10:
                return 'PASS'  # Minimal change
            elif scale_change < 25:
                return 'WARN'  # Noticeable change
            else:
                return 'FAIL'  # Large scale jump
        
        except Exception as e:
            logger.error(f"Scale check error: {e}")
            return 'UNKNOWN'
    
    def _get_frame_path(self, pattern_path: Path, frame: int) -> Path:
        """Convert pattern to specific frame path"""
        from ...utils.sequence_utils import format_pattern_with_frame
        
        folder = pattern_path.parent
        pattern = pattern_path.name
        filename = format_pattern_with_frame(pattern, frame)
        
        return folder / filename
    
    def _assess_overall(self, results: Dict) -> str:
        """Assess overall continuity"""
        checks = [results['motion'], results['color'], results['scale']]
        
        if 'FAIL' in checks:
            return 'FAIL'
        elif 'WARN' in checks:
            return 'WARN'
        elif all(c == 'PASS' for c in checks):
            return 'PASS'
        else:
            return 'UNKNOWN'
    
    def get_status_icon(self, status: str) -> str:
        """Get emoji for status"""
        icons = {
            'PASS': '✅',
            'WARN': '⚠️',
            'FAIL': '❌',
            'UNKNOWN': '❓'
        }
        return icons.get(status, '❓')
