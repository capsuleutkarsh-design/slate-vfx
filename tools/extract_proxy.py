methods = [
    'start_proxy_render',
    '_start_proxy_render_worker',
    'on_proxy_render_progress',
    'on_proxy_render_finished'
]
import sys
sys.path.append('d:/Soft/Slate/V0040/scripts')
from extract_cache import extract_methods_to_delegates

extract_methods_to_delegates(
    'd:/Soft/Slate/V0040/slate/gui/tabs/shot_review_tab.py',
    methods,
    'd:/Soft/Slate/V0040/slate/gui/tabs/shot_review/controllers/proxy_render_controller.py'
)
