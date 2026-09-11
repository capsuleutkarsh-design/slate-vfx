
import logging
logger = logging.getLogger(__name__)

def previous_shot(tab):
    """Select previous shot in list"""
    current_row = tab.shot_list.currentRow()
    if current_row > 0:
        tab.shot_list.setCurrentRow(current_row - 1)
        tab.on_shot_selected(tab.shot_list.currentItem())

def next_shot(tab):
    """Select next shot in list"""
    current_row = tab.shot_list.currentRow()
    if current_row < tab.shot_list.count() - 1:
        tab.shot_list.setCurrentRow(current_row + 1)
        tab.on_shot_selected(tab.shot_list.currentItem())

def next_page(tab):
    """Load next page of shots"""
    max_page = (len(tab.all_shots) + tab.shots_per_page - 1) // tab.shots_per_page - 1
    if tab.current_page < max_page:
        tab.current_page += 1
        tab.update_shot_list()

def prev_page(tab):
    """Load previous page of shots"""
    if tab.current_page > 0:
        tab.current_page -= 1
        tab.update_shot_list()
