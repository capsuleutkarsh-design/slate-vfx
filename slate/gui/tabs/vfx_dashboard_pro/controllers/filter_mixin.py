from PySide6.QtCore import QItemSelectionModel, QTimer
import qasync
class DashboardFilterMixin:
    """
    Mixin class to handle Dashboard filtering, search, and table selections.
    """
    def populate_filters(self):
        if getattr(self, "all_shots", None) is None:
            return
        statuses = sorted(list(set(s.status for s in self.all_shots if s.status)))
        self.update_combo(self.status_filter, statuses, "All Status")
        
    def update_combo(self, combo, items, default_text):
        if not combo:
            return
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(default_text)
        combo.addItems(items)
        if current in items:
            combo.setCurrentText(current)
        combo.blockSignals(False)
        
    def on_header_filter_changed(self, col_idx, value):
        self.apply_filters()
        
    def on_project_data_updated(self):
        """
        Called by PollWorker - the fallback when the change feed is not
        working - when something in the project changed. It cannot say what,
        so the project is read once and merged: unsaved edits are kept and the
        selection stays (see live_update_mixin).
        """
        sender = self.sender()
        if sender is not None and getattr(self, "poll_worker", None) and sender is not self.poll_worker:
            return
        if getattr(self, "_is_closing", False):
            return
        self.log("Merging an external update...")
        self._queue_live_full()
        
    def _schedule_filter_update(self):
        """Triggered by search input text changes, debounced to avoid UI freezes."""
        if hasattr(self, '_search_debounce_timer'):
            self._search_debounce_timer.start()
        else:
            self.apply_filters()

    def apply_filters(self):
        if not hasattr(self, "search_input") or not hasattr(self, "status_filter"):
            return
            
        search_text = self.search_input.text().lower()
        status = self.status_filter.currentText()
        def to_lower(v):
            return str(v or "").lower()
            
        # The hidden "?" semantic search (and its model download) was removed
        # on the user's decision; a search is the plain filter below.
        
        header_filters = self.header_view.active_filters if hasattr(self, "header_view") else {}
        
        self.displayed_shots = []
        for s in self.all_shots:
            if status != "All Status" and s.status != status:
                continue

            # Scope filtering (Item 4.2)
            scope_mode = "all"
            if hasattr(self, "scope_combo") and self.scope_combo:
                scope_mode = self.scope_combo.currentData() or "all"

            if scope_mode == "my_shots":
                identities = self._artist_identity_candidates() if hasattr(self, "_artist_identity_candidates") else set()
                try:
                    names = s.get_all_artists()
                except Exception:
                    names = [getattr(s, "assigned_artist", "")]
                if not any(str(n or "").strip().lower() in identities for n in names if n):
                    continue
            elif str(scope_mode).startswith("dept:"):
                dept_family = str(scope_mode).split(":", 1)[1]
                from slate.core.domain.departments import load_departments
                depts_in_fam = [d.key for d in load_departments() if d.family == dept_family]
                has_work = False
                for dk in depts_in_fam:
                    dept = s.dept(dk)
                    st = (getattr(dept, "status", "") or "").strip().upper()
                    if st and st not in ("N/A", "OMIT", "-", "NONE"):
                        has_work = True
                        break
                    # A shot assigned to this department, or bid for it, counts
                    # as its work even before anybody has set a status. Chasing
                    # exactly those shots is most of what a lead does.
                    if str(getattr(dept, "artist", "") or "").strip():
                        has_work = True
                        break
                    try:
                        if float(getattr(dept, "bid_days", 0) or 0) > 0:
                            has_work = True
                            break
                    except (TypeError, ValueError):
                        pass
                if not has_work:
                    continue
            
            if search_text:
                shot_match = search_text in to_lower(getattr(s, "shot_name", ""))
                
                # Search every department's artist and target, whatever the
                # configured department list happens to be.
                dept_values = []
                for dept in getattr(s, "departments", {}).values():
                    dept_values.append(getattr(dept, "artist", ""))
                    dept_values.append(getattr(dept, "target", ""))

                artist_match = (
                    any(search_text in to_lower(v) for v in dept_values) or
                    search_text in to_lower(getattr(s, "assigned_artist", "")) or
                    search_text in to_lower(getattr(s, "target", ""))
                )
                if not (shot_match or artist_match):
                    continue
            
            match_header = True
            for col_idx, filter_val in header_filters.items():
                getter = self.table_model.COLUMNS[col_idx][2]
                val = str(getter(s))
                if val != filter_val:
                    match_header = False
                    break
            if not match_header:
                continue
                
            # Advanced Query Builder Logic
            advanced_rules = getattr(self, "advanced_query_rules", [])
            if advanced_rules:
                match_type = getattr(self, "advanced_query_match_type", "AND")
                passed_advanced = False if match_type == "OR" else True
                
                for rule in advanced_rules:
                    field = rule.get("field")
                    op = rule.get("operator")
                    val = rule.get("value", "").lower()
                    
                    # Map field string to shot object attribute
                    field_map = {
                        "Shot Code": "shot_name",
                        "Sequence": "sequence",
                        "Status": "status",
                        "Client Status": "client_status",
                        "Assigned Artist": "assigned_artist",
                        "Priority": "priority",
                        "Description": "description",
                        "Internal Comment": "internal_comment",
                        "Client Feedback": "client_feedback"
                    }
                    
                    attr = field_map.get(field)
                    if not attr:
                        continue
                        
                    shot_val = getattr(s, attr, "")
                    
                    # Handle None values
                    if shot_val is None:
                        shot_val = ""
                        
                    # Evaluate operator
                    rule_passed = False
                    
                    if op == "Is Empty":
                        rule_passed = (str(shot_val).strip() == "")
                    elif op == "Is Not Empty":
                        rule_passed = (str(shot_val).strip() != "")
                    else:
                        shot_val_lower = str(shot_val).lower()
                        if op == "Equals":
                            rule_passed = (shot_val_lower == val)
                        elif op == "Not Equals":
                            rule_passed = (shot_val_lower != val)
                        elif op == "Contains":
                            rule_passed = (val in shot_val_lower)
                        elif op == "Does Not Contain":
                            rule_passed = (val not in shot_val_lower)
                            
                    if match_type == "AND" and not rule_passed:
                        passed_advanced = False
                        break
                    elif match_type == "OR" and rule_passed:
                        passed_advanced = True
                        break
                        
                if not passed_advanced:
                    continue
                
            self.displayed_shots.append(s)
        
        self.update_table()
        if hasattr(self, "start_thumbnail_loading"):
            self.start_thumbnail_loading()
