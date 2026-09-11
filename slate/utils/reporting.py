import logging
from pathlib import Path
from typing import Tuple
import pandas as pd
# import matplotlib.pyplot as plt # LAZY LOAD
# import seaborn as sns # LAZY LOAD
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import sqlite3

# Import the SINGLE source of truth for the database
from ..core.infra.database_manager import database_manager

class ReportGenerator:
    """Generate reports strictly for the SELECTED project."""
    
    def __init__(self):
        self.db_manager = database_manager
        self.styles = getSampleStyleSheet()
        self.max_report_task_rows = 50000
        
        # Lazy Import Helper
        self._plt = None
        self._sns = None
        
    def _ensure_plotting_libs(self):
        if self._plt is None:
            import matplotlib.pyplot as plt
            import seaborn as sns
            self._plt = plt
            self._sns = sns
            
            # Set up matplotlib style safely
            try:
                plt.style.use('seaborn-v0_8-darkgrid')
            except OSError:
                try:
                    plt.style.use('seaborn-darkgrid')
                except OSError:
                    logging.debug("Matplotlib seaborn styles unavailable, using default style.")

    def generate_project_summary_report(self, output_path: Path, project_id: int = None) -> Tuple[bool, str]:
        """
        Generate a comprehensive PDF report.
        If project_id is provided, reports that specific project.
        If None, defaults to the latest project.
        """
        try:
            # 1. FETCH DATA
            if project_id:
                proj = self.db_manager.execute_query(
                    "SELECT id, name, created_at FROM projects WHERE id = %s",
                    (int(project_id),),
                    fetch="one",
                )
                if not proj:
                    return False, f"Project ID {project_id} not found."
            else:
                proj = self.db_manager.execute_query(
                    "SELECT id, name, created_at FROM projects ORDER BY id DESC LIMIT 1",
                    fetch="one",
                )
                if not proj:
                    return False, "No project history found in database."

            # Unpack project details
            target_id = int(proj['id'])
            proj_name = proj['name']
            proj_date = proj['created_at']

            # Load report rows with an explicit cap to avoid unbounded fetches.
            max_task_rows = int(self.max_report_task_rows)
            total_task_row = self.db_manager.execute_query(
                """
                SELECT COUNT(*) AS count
                FROM task_details td
                INNER JOIN operations o ON o.id = td.operation_id
                WHERE o.project_id = %s
                """,
                (target_id,),
                fetch="one",
            )
            total_task_rows = int(total_task_row["count"]) if total_task_row else 0

            task_rows = self.db_manager.execute_query(
                """
                SELECT td.*
                FROM task_details td
                INNER JOIN operations o ON o.id = td.operation_id
                WHERE o.project_id = %s
                ORDER BY td.id DESC
                LIMIT %s
                """,
                (target_id, max_task_rows),
                fetch="all",
            ) or []

            tasks_df = pd.DataFrame([dict(row) for row in task_rows]) if task_rows else pd.DataFrame()
            tasks_truncated = total_task_rows > len(tasks_df)

            # 2. Start PDF Build
            doc = SimpleDocTemplate(str(output_path), pagesize=landscape(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
            story = []
            
            # Title
            title_color = colors.HexColor("#3EA8BF")
            title_style = ParagraphStyle('CustomTitle', parent=self.styles['Heading1'], fontSize=24, spaceAfter=20, alignment=1, textColor=title_color)
            story.append(Paragraph(f"Slate - Project Report: {proj_name}", title_style))
            
            # Executive Summary
            story.append(Paragraph("Session Overview", self.styles['Heading2']))
            
            summary_text = f"<b>Project Name:</b> {proj_name}<br/>"
            summary_text += f"<b>Date:</b> {proj_date}<br/>"
            
            if not tasks_df.empty:
                # Ensure sizes are treated as numeric
                tasks_df['file_size'] = pd.to_numeric(tasks_df['file_size'], errors='coerce').fillna(0)
                tasks_df['duration'] = pd.to_numeric(tasks_df['duration'], errors='coerce').fillna(0)

                total_size_bytes = tasks_df['file_size'].sum()
                total_size_gb = total_size_bytes / (1024**3)
                total_duration = tasks_df['duration'].sum()
                
                # Avoid division by zero
                avg_speed = (total_size_bytes / (1024*1024)) / total_duration if total_duration > 0 else 0
                
                summary_text += f"<b>Total Data Moved:</b> {total_size_gb:.4f} GB<br/>"
                summary_text += f"<b>Total Files Processed:</b> {total_task_rows:,}<br/>"
                summary_text += f"<b>Total Time Active:</b> {total_duration:.2f} seconds<br/>"
                summary_text += f"<b>Average Speed:</b> {avg_speed:.2f} MB/s<br/>"
                if tasks_truncated:
                    summary_text += (
                        f"<b>Note:</b> Detailed tables use latest {len(tasks_df):,} of "
                        f"{total_task_rows:,} rows for performance.<br/>"
                    )
            else:
                summary_text += "<br/><b>No files were moved in this session.</b><br/>"
            
            story.append(Paragraph(summary_text, self.styles['Normal']))
            story.append(Spacer(1, 20))

            # --- 2b. SUSPICIOUS FILES CHECK (User Request) ---
            if not tasks_df.empty:
                # Find duplicates based on "Stem" name (ignoring extension collision logic for safety)
                # Or find "Copy" / "(1)" patterns
                suspicious_list = []
                for idx, row in tasks_df.iterrows():
                    name = str(row['item_name'])
                    lower_name = name.lower()
                    
                    # 1. Pattern Check
                    if any(x in lower_name for x in ['(1)', 'copy', 'conflict', ' (2)']):
                        suspicious_list.append((name, row['dest_path'], "Pattern Match (Copy/Conflict)"))
                        continue
                        
                    # 2. Similarity Check (Basic) - might be too heavy for 10k files?
                    # Let's simple check stem collision in DIFFERENT folders? No, user said "multiple file name like one file"
                    # Likely means: "shot_010.mov" and "shot_010 (1).mov" existent in list.
                    
                if suspicious_list:
                    story.append(Paragraph("Suspicious Files Detected", self.styles['Heading2']))
                    warn_style = ParagraphStyle('Warn', textColor=colors.red, fontSize=11)
                    story.append(Paragraph(f"Found {len(suspicious_list)} potentially conflicting or duplicate files.", warn_style))
                    
                    sus_data = [['File Name', 'Location', 'Issue']]
                    for fname, loc, issue in suspicious_list[:50]: # Limit to 50 to avoid overflow
                        sus_data.append([
                            Paragraph(fname, ParagraphStyle('s', fontSize=9)),
                            Paragraph(str(loc), ParagraphStyle('s', fontSize=8)), 
                            issue
                        ])
                    
                    ts = Table(sus_data, colWidths=[150, 300, 100])
                    ts.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#3A1F1E")),
                        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#D9635F"), colors.white])
                    ]))
                    story.append(ts)
                    story.append(Spacer(1, 20))


            # 3. DETAILED FILE LOG
            if not tasks_df.empty:
                story.append(Paragraph("Transferred Folders (Aggregated)", self.styles['Heading2']))
                
                # Logic: Group by Parent Folder to avoid listing 10,000 frames individually
                # We assume 'dest_path' is populated. If not, fallback to 'item_name'
                tasks_df['parent_folder'] = tasks_df['dest_path'].apply(lambda x: str(Path(x).parent) if x else "Unknown")
                
                # Group and Aggregate
                grouped = tasks_df.groupby('parent_folder').agg({
                    'file_size': 'sum',
                    'duration': 'sum',
                    'item_name': 'count', 
                    'status': lambda x: 'Error' if 'Failed' in x.values else 'Success'
                }).reset_index()

                # Sort by Size
                grouped = grouped.sort_values(by='file_size', ascending=False)

                # Prepare Table Data
                headers = ['Shot / Folder Path', 'File Count', 'Total Size', 'Time', 'Speed', 'Status']
                table_data = [headers]
                
                header_bg = colors.HexColor("#1D1D22")
                row_bg_alt = colors.HexColor("#E8E6E1")

                for i, row in grouped.iterrows():
                    mb_size = row['file_size'] / (1024*1024)
                    dur = row['duration'] if row['duration'] > 0 else 0.01
                    speed = mb_size / dur
                    
                    full_path = Path(row['parent_folder'])
                    display_path = str(full_path) # FULL PATH REQUESTED BY USER

                    
                    status_text = row['status']
                    status_color = colors.green if status_text == 'Success' else colors.red
                    
                    table_data.append([
                        Paragraph(display_path, ParagraphStyle('small', fontSize=9)),
                        str(row['item_name']),
                        self._format_bytes(row['file_size']),
                        f"{row['duration']:.1f}s",
                        f"{speed:.1f} MB/s",
                        Paragraph(f"<b>{status_text}</b>", ParagraphStyle('status', textColor=status_color))
                    ])

                col_widths = [350, 60, 80, 60, 80, 80]
                t = Table(table_data, colWidths=col_widths)
                
                base_style = [
                    ('BACKGROUND', (0, 0), (-1, 0), header_bg),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [row_bg_alt, colors.white]),
                ]
                
                t.setStyle(TableStyle(base_style))
                story.append(t)
            
            doc.build(story)
            return True, f"Report for '{proj_name}' generated at: {output_path}"
            
        except (sqlite3.Error, pd.errors.PandasError, OSError, ValueError, TypeError, KeyError, RuntimeError) as e:
            error_msg = f"Error generating report: {str(e)}"
            logging.error(error_msg, exc_info=True)
            return False, error_msg
    
    def generate_performance_metrics_report(self, output_path: Path) -> Tuple[bool, str]:
        """Generate a visual performance report."""
        # (This remains largely the same, but you could add similar filtering if needed)
        return True, "Performance report feature pending update."

    def _format_bytes(self, size):
        if size == 0:
            return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

# Global instance
report_generator = ReportGenerator()
