import logging
logger = logging.getLogger(__name__)

def cache_all_frames(tab):
    logger.info("Cache all frames requested.")
    pass

def _cancel_cache_worker(tab):
    if hasattr(tab, '_cache_worker') and tab._cache_worker:
        try:
            tab._cache_worker.cancel()
        except Exception as e:
            logger.debug(f"Error cancelling cache worker: {e}")
        tab._cache_worker = None

def _on_cache_progress(tab, cached_count, total_frames):
    pass

def _on_cache_finished(tab, cached_count, total_frames, shot_id):
    update_cache_stats(tab)

def clear_cache(tab):
    if hasattr(tab, 'comparison_viewer') and hasattr(tab.comparison_viewer, 'cache'):
        tab.comparison_viewer.cache.clear()
        update_cache_stats(tab)

def update_cache_stats(tab):
    try:
        if not hasattr(tab, 'comparison_viewer') or not hasattr(tab.comparison_viewer, 'cache'):
            return
            
        cache = tab.comparison_viewer.cache
        num_frames = len(cache)
        estimated_mb = num_frames * 5  # approx 5mb per frame for display
        max_mb = 4096
        pct = int((estimated_mb / max_mb) * 100) if max_mb > 0 else 0
        
        if hasattr(tab, 'cache_stats_label') and tab.cache_stats_label:
            tab.cache_stats_label.setText(f"Cache: {estimated_mb} MB / {max_mb} MB ({pct}%)")
    except Exception as e:
        logger.error(f"Error updating cache stats: {e}")
