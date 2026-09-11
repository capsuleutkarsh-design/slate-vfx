import os
import re

directories_to_scan = [
    r"d:\Soft\UTCAP\V0040\ut_vfx",
    r"d:\Soft\UTCAP\V0040\ut_messenger",
    r"d:\Soft\UTCAP\V0040\tools"
]

replacements = [
    (r'(?<!Qt\.ItemDataRole\.)Qt\.UserRole', 'Qt.ItemDataRole.UserRole'),
    (r'(?<!Qt\.ItemDataRole\.)Qt\.DisplayRole', 'Qt.ItemDataRole.DisplayRole'),
    (r'(?<!Qt\.ItemDataRole\.)Qt\.DecorationRole', 'Qt.ItemDataRole.DecorationRole'),
    (r'(?<!QMessageBox\.StandardButton\.)QMessageBox\.Yes', 'QMessageBox.StandardButton.Yes'),
    (r'(?<!QMessageBox\.StandardButton\.)QMessageBox\.No', 'QMessageBox.StandardButton.No'),
    (r'(?<!QMessageBox\.Icon\.)QMessageBox\.Warning', 'QMessageBox.Icon.Warning'),
    (r'(?<!QMessageBox\.Icon\.)QMessageBox\.Information', 'QMessageBox.Icon.Information'),
    (r'(?<!QDialog\.DialogCode\.)QDialog\.Accepted', 'QDialog.DialogCode.Accepted'),
    (r'(?<!QDialog\.DialogCode\.)QDialog\.Rejected', 'QDialog.DialogCode.Rejected'),
]

files_modified = 0

for d in directories_to_scan:
    for root, dirs, files in os.walk(d):
        for file in files:
            if file.endswith('.py'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                new_content = content
                for pattern, replacement in replacements:
                    new_content = re.sub(pattern, replacement, new_content)
                
                if new_content != content:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    files_modified += 1
                    print(f"Fixed enums in {path}")

print(f"Total files modified: {files_modified}")
