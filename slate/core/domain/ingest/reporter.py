"""
SECURE Flight Recorder - Report Generator.
Generates detailed HTML reports of ingest operations.
"""
from pathlib import Path
from datetime import datetime
from typing import List, Dict

class ReportGenerator:
    @staticmethod
    def generate_html_report(operations: List[Dict], output_dir: Path) -> Path:
        """
        Generates an HTML report from a list of operation dictionaries.
        Op Dict: { 'timestamp': str, 'source': str, 'dest': str, 'status': str, 'size': str }
        """
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = output_dir / f"Ingest_Report_{timestamp}.html"
        
        success_count = sum(1 for op in operations if op['status'] == 'Success')
        fail_count = sum(1 for op in operations if op['status'] != 'Success')
        total_size = sum(op.get('bytes', 0) for op in operations)
        total_size_mb = total_size / (1024*1024)
        
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; background: #1D1D22; color: #E8E6E1; padding: 20px; }}
                h1 {{ color: #3EA8BF; }}
                .stats {{ background: #26262D; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
                table {{ width: 100%; border-collapse: collapse; background: #1D1D22; }}
                th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #26262D; }}
                th {{ background: #26262D; color: #B4B1AA; }}
                .success {{ color: #5FBF8F; }}
                .fail {{ color: #D9635F; }}
            </style>
        </head>
        <body>
            <h1>[REPORT] Ingest Flight Recorder</h1>
            <div class="stats">
                <strong>Date:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}<br>
                <strong>Total Files:</strong> {len(operations)}<br>
                <strong>Success:</strong> {success_count} | <strong>Failed:</strong> {fail_count}<br>
                <strong>Total Size:</strong> {total_size_mb:.2f} MB
            </div>
            <table>
                <tr>
                    <th>Time</th>
                    <th>Source</th>
                    <th>Destination</th>
                    <th>Status</th>
                    <th>Context</th>
                </tr>
        """
        
        for op in operations:
            status_class = "success" if op['status'] == 'Success' else "fail"
            html_content += f"""
                <tr>
                    <td>{op['timestamp']}</td>
                    <td>{op['source']}</td>
                    <td>{op['dest']}</td>
                    <td class="{status_class}">{op['status']}</td>
                    <td>{op.get('context', '-')}</td>
                </tr>
            """
            
        html_content += """
            </table>
        </body>
        </html>
        """
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        return report_file