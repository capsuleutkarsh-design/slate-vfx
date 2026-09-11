import hashlib
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime


class AssetTracker:
    """Track and manage digital assets with comprehensive metadata."""
    
    def __init__(self, db_manager=None):
        self.db_manager = db_manager  # Accept None initially, will be set later
        self.asset_cache = {}  # Cache for performance
        self.tracked_projects = set()  # Projects currently being tracked
    
    def set_db_manager(self, db_manager):
        """Set the database manager after initialization."""
        self.db_manager = db_manager
    
    def track_asset(self, project_id: int, asset_path: Path, asset_type: str = "generic",
                   metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Track a new asset with metadata."""
        try:
            if not asset_path.exists():
                logging.warning(f"Cannot track non-existent asset: {asset_path}")
                return False
            
            # Calculate asset size and checksum
            asset_size = self._get_path_size(asset_path)
            asset_checksum = self._calculate_checksum(asset_path)
            
            # Prepare asset metadata
            asset_metadata = metadata or {}
            asset_metadata.update({
                'size': asset_size,
                'checksum': asset_checksum,
                'created_at': datetime.now().isoformat(),
                'modified_at': datetime.fromtimestamp(asset_path.stat().st_mtime).isoformat()
            })
            
            # Record asset in database if db_manager is available
            if self.db_manager:
                asset_id = self.db_manager.record_asset(
                    project_id, 
                    asset_path.name, 
                    asset_type, 
                    str(asset_path), 
                    asset_size, 
                    'active',
                    asset_metadata
                )
            else:
                # Just store in cache if no database manager
                cache_key = f"{project_id}_{asset_path}"
                self.asset_cache[cache_key] = {
                    'id': len(self.asset_cache) + 1,
                    'path': str(asset_path),
                    'size': asset_size,
                    'checksum': asset_checksum,
                    'metadata': asset_metadata
                }
                asset_id = len(self.asset_cache)
            
            logging.info(f"Tracked asset: {asset_path.name} (ID: {asset_id}) in project {project_id}")
            return True
            
        except Exception as e:
            error_msg = f"Error tracking asset {asset_path}: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return False
    
    def track_folder_structure(self, project_id: int, root_path: Path) -> int:
        """Recursively track all assets in a folder structure."""
        assets_tracked = 0
        
        try:
            for item in root_path.rglob('*'):
                if item.is_file():
                    # Determine asset type based on extension
                    asset_type = self._determine_asset_type(item)
                    
                    # Track the asset
                    if self.track_asset(project_id, item, asset_type):
                        assets_tracked += 1
                elif item.is_dir():
                    # Track directory as well
                    if self.track_asset(project_id, item, "directory"):
                        assets_tracked += 1
            
            logging.info(f"Tracked {assets_tracked} assets in folder structure: {root_path}")
            return assets_tracked
            
        except Exception as e:
            error_msg = f"Error tracking folder structure {root_path}: {str(e)}"
            logging.exception(error_msg, exc_info=True)
            return assets_tracked
    
    def _determine_asset_type(self, file_path: Path) -> str:
        """Determine asset type based on file extension."""
        extension = file_path.suffix.lower()
        
        # VFX-specific extensions
        if extension in ['.exr', '.dpx', '.tif', '.tiff']:
            return "scan_image"
        elif extension in ['.mov', '.mp4', '.avi', '.mkv']:
            return "scan_video"
        elif extension in ['.abc', '.fbx', '.obj', '.ma', '.mb']:
            return "model_geometry"
        elif extension in ['.ma', '.mb', '.hip', '.scn']:
            return "scene_file"
        elif extension in ['.nk', '.hrox']:
            return "comp_script"
        elif extension in ['.py', '.mel', '.sh']:
            return "script"
        elif extension in ['.txt', '.log', '.json', '.xml', '.yaml', '.yml']:
            return "document"
        elif extension in ['.doc', '.docx', '.pdf']:
            return "documentation"
        else:
            return "generic"
    
    def validate_asset_integrity(self, asset_path: Path, expected_checksum: str) -> bool:
        """Validate asset integrity against stored checksum."""
        try:
            if not asset_path.exists():
                return False
            
            actual_checksum = self._calculate_checksum(asset_path)
            return actual_checksum == expected_checksum
            
        except Exception as e:
            logging.exception(f"Error validating asset integrity {asset_path}: {e}")
            return False
    
    def get_asset_by_path(self, project_id: int, asset_path: Path) -> Optional[Dict[str, Any]]:
        """Get asset information by path."""
        try:
            # Check cache first
            cache_key = f"{project_id}_{asset_path}"
            if cache_key in self.asset_cache:
                return self.asset_cache[cache_key]
            
            # Query database if available
            if self.db_manager:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT * FROM assets 
                        WHERE project_id = ? AND path = ?
                    """, (project_id, str(asset_path)))
                    
                    row = cursor.fetchone()
                    if row:
                        asset_info = dict(row)
                        
                        # Add to cache
                        self.asset_cache[cache_key] = asset_info
                        return asset_info
            
            return None
            
        except Exception as e:
            logging.exception(f"Error getting asset by path {asset_path}: {e}")
            return None
    
    def get_assets_by_type(self, project_id: int, asset_type: str) -> List[Dict[str, Any]]:
        """Get all assets of a specific type for a project."""
        try:
            if self.db_manager:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT * FROM assets 
                        WHERE project_id = ? AND asset_type = ?
                        ORDER BY created_at DESC
                    """, (project_id, asset_type))
                    
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]
            else:
                # Return cached assets of the specified type
                matching_assets = []
                for key, asset_info in self.asset_cache.items():
                    if asset_info.get('metadata', {}).get('asset_type') == asset_type:
                        matching_assets.append(asset_info)
                return matching_assets
                
        except Exception as e:
            logging.exception(f"Error getting assets by type {asset_type}: {e}")
            return []
    
    def get_project_assets(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all assets for a project."""
        try:
            if self.db_manager:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT * FROM assets 
                        WHERE project_id = ?
                        ORDER BY created_at DESC
                    """, (project_id,))
                    
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]
            else:
                # Return cached assets for the project
                matching_assets = []
                for key, asset_info in self.asset_cache.items():
                    if str(project_id) in key:
                        matching_assets.append(asset_info)
                return matching_assets
                
        except Exception as e:
            logging.exception(f"Error getting project assets {project_id}: {e}")
            return []
    
    def update_asset_status(self, asset_path: Path, new_status: str) -> bool:
        """Update asset status."""
        try:
            if self.db_manager:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE assets 
                        SET status = ?, modified_at = ?
                        WHERE path = ?
                    """, (new_status, datetime.now().isoformat(), str(asset_path)))
                    
                    conn.commit()
                    
                    # Clear cache for this asset
                    for key in list(self.asset_cache.keys()):
                        if str(asset_path) in key:
                            del self.asset_cache[key]
                    
                    return cursor.rowcount > 0
            else:
                # Update in cache if no database
                for key in list(self.asset_cache.keys()):
                    if str(asset_path) in key:
                        self.asset_cache[key]['status'] = new_status
                        self.asset_cache[key]['modified_at'] = datetime.now().isoformat()
                        return True
                return False
                
        except Exception as e:
            logging.exception(f"Error updating asset status {asset_path}: {e}")
            return False
    
    def mark_asset_inactive(self, asset_path: Path) -> bool:
        """Mark an asset as inactive (soft delete)."""
        return self.update_asset_status(asset_path, 'inactive')
    
    def delete_asset_record(self, asset_path: Path) -> bool:
        """Permanently delete asset record from database."""
        try:
            if self.db_manager:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM assets WHERE path = ?", (str(asset_path),))
                    conn.commit()
                    
                    # Clear cache for this asset
                    for key in list(self.asset_cache.keys()):
                        if str(asset_path) in key:
                            del self.asset_cache[key]
                    
                    return cursor.rowcount > 0
            else:
                # Remove from cache if no database
                deleted = False
                for key in list(self.asset_cache.keys()):
                    if str(asset_path) in key:
                        del self.asset_cache[key]
                        deleted = True
                return deleted
                
        except Exception as e:
            logging.exception(f"Error deleting asset record {asset_path}: {e}")
            return False
    
    def get_asset_statistics(self, project_id: Optional[int] = None) -> Dict[str, Any]:
        """Get asset statistics for a project or overall."""
        try:
            if self.db_manager:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    
                    if project_id:
                        # Get stats for specific project
                        cursor.execute("""
                            SELECT 
                                asset_type,
                                COUNT(*) as count,
                                SUM(size) as total_size,
                                AVG(size) as avg_size
                            FROM assets 
                            WHERE project_id = ? AND status = 'active'
                            GROUP BY asset_type
                        """, (project_id,))
                    else:
                        # Get overall stats
                        cursor.execute("""
                            SELECT 
                                asset_type,
                                COUNT(*) as count,
                                SUM(size) as total_size,
                                AVG(size) as avg_size
                            FROM assets 
                            WHERE status = 'active'
                            GROUP BY asset_type
                        """)
                    
                    rows = cursor.fetchall()
                    asset_stats = [dict(row) for row in rows]
                    
                    # Calculate totals
                    total_count = sum(stat['count'] for stat in asset_stats)
                    total_size = sum(stat['total_size'] or 0 for stat in asset_stats)
                    
                    return {
                        'asset_types': asset_stats,
                        'totals': {
                            'count': total_count,
                            'size_bytes': total_size,
                            'size_formatted': self._format_bytes(total_size)
                        }
                    }
            else:
                # Return statistics from cache
                asset_types = {}
                total_count = 0
                total_size = 0
                
                for asset_info in self.asset_cache.values():
                    asset_type = asset_info.get('metadata', {}).get('asset_type', 'unknown')
                    size = asset_info.get('size', 0)
                    
                    if asset_type not in asset_types:
                        asset_types[asset_type] = {'count': 0, 'total_size': 0, 'avg_size': 0}
                    
                    asset_types[asset_type]['count'] += 1
                    asset_types[asset_type]['total_size'] += size
                    total_count += 1
                    total_size += size
                
                # Calculate averages
                for asset_type, stats in asset_types.items():
                    if stats['count'] > 0:
                        stats['avg_size'] = stats['total_size'] / stats['count']
                
                return {
                    'asset_types': [{'asset_type': k, **v} for k, v in asset_types.items()],
                    'totals': {
                        'count': total_count,
                        'size_bytes': total_size,
                        'size_formatted': self._format_bytes(total_size)
                    }
                }
                
        except Exception as e:
            logging.exception(f"Error getting asset statistics: {e}")
            return {'asset_types': [], 'totals': {'count': 0, 'size_bytes': 0, 'size_formatted': '0 B'}}
    
    def _get_path_size(self, path: Path) -> int:
        """Get total size of file or directory."""
        if path.is_file():
            return path.stat().st_size
        else:
            return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    
    def _calculate_checksum(self, path: Path) -> str:
        """Calculate MD5 checksum for file or directory."""
        if path.is_file():
            # For files, calculate MD5 checksum
            hash_md5 = hashlib.md5()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        else:
            # For directories, hash the list of files and their sizes
            hash_md5 = hashlib.md5()
            for file_path in sorted(path.rglob('*')):
                if file_path.is_file():
                    file_info = f"{file_path.name}:{file_path.stat().st_size}"
                    hash_md5.update(file_info.encode())
            return hash_md5.hexdigest()
    
    def _format_bytes(self, size: float) -> str:
        """Format bytes to human readable format."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"


class AssetIntegrationManager:
    """Manage integration with external asset management systems."""
    
    def __init__(self, asset_tracker: AssetTracker):
        self.asset_tracker = asset_tracker
        self.integration_configs = {}
    
    def configure_integration(self, system_name: str, config: Dict[str, Any]):
        """Configure integration with an asset management system."""
        self.integration_configs[system_name] = config
        logging.info(f"Configured integration with {system_name}")
    
    def sync_to_external_system(self, project_id: int, system_name: str) -> bool:
        """Sync project assets to external asset management system."""
        try:
            if system_name not in self.integration_configs:
                logging.error(f"No configuration found for {system_name}")
                return False
            
            # Get project assets
            assets = self.asset_tracker.get_project_assets(project_id)
            
            # Get integration config
            config = self.integration_configs[system_name]
            
            # This would be specific to each system
            # For example, if integrating with Shotgun:
            if system_name.lower() == 'shotgun':
                return self._sync_to_shotgun(assets, config)
            elif system_name.lower() == 'ftrack':
                return self._sync_to_ftrack(assets, config)
            elif system_name.lower() == 'rv':
                return self._sync_to_rv(assets, config)
            else:
                logging.warning(f"Unsupported asset management system: {system_name}")
                return False
                
        except Exception as e:
            logging.exception(f"Error syncing to {system_name}: {e}")
            return False
    
    def _sync_to_shotgun(self, assets: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
        """Sync assets to Shotgun."""
        # Implementation would require Shotgun API
        # This is a placeholder
        logging.info(f"Syncing {len(assets)} assets to Shotgun")
        return True
    
    def _sync_to_ftrack(self, assets: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
        """Sync assets to FTrack."""
        # Implementation would require FTrack API
        # This is a placeholder
        logging.info(f"Syncing {len(assets)} assets to FTrack")
        return True
    
    def _sync_to_rv(self, assets: List[Dict[str, Any]], config: Dict[str, Any]) -> bool:
        """Sync assets to RV for review."""
        # Implementation would require RV API
        # This is a placeholder
        logging.info(f"Syncing {len(assets)} assets to RV")
        return True
    
    def import_from_external_system(self, project_id: int, system_name: str) -> bool:
        """Import assets from external asset management system."""
        try:
            if system_name not in self.integration_configs:
                logging.error(f"No configuration found for {system_name}")
                return False
            
            # Get integration config
            config = self.integration_configs[system_name]
            
            # This would be specific to each system
            if system_name.lower() == 'shotgun':
                assets = self._import_from_shotgun(config)
            elif system_name.lower() == 'ftrack':
                assets = self._import_from_ftrack(config)
            else:
                logging.warning(f"Unsupported asset management system: {system_name}")
                return False
            
            # Track imported assets
            for asset in assets:
                asset_path = Path(asset.get('path', ''))
                asset_type = asset.get('type', 'generic')
                metadata = asset.get('metadata', {})
                
                self.asset_tracker.track_asset(project_id, asset_path, asset_type, metadata)
            
            return True
            
        except Exception as e:
            logging.exception(f"Error importing from {system_name}: {e}")
            return False
    
    def _import_from_shotgun(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Import assets from Shotgun."""
        # Implementation would require Shotgun API
        # This is a placeholder
        logging.info("Importing assets from Shotgun")
        return []
    
    def _import_from_ftrack(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Import assets from FTrack."""
        # Implementation would require FTrack API
        # This is a placeholder
        logging.info("Importing assets from FTrack")
        return []


# Global instance - will be initialized after database manager is available
asset_tracker = AssetTracker()
asset_integration_manager = AssetIntegrationManager(asset_tracker)