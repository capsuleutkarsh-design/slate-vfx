methods = [
    'previous_shot',
    'next_shot',
    'next_page',
    'prev_page'
]
import sys
sys.path.append('d:/Soft/UTCAP/V0040/scripts')
from extract_cache import extract_methods_to_delegates

extract_methods_to_delegates(
    'd:/Soft/UTCAP/V0040/ut_vfx/gui/tabs/shot_review_tab.py',
    methods,
    'd:/Soft/UTCAP/V0040/ut_vfx/gui/tabs/shot_review/controllers/pagination_controller.py'
)
