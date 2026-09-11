import ast
import re

def extract_methods_to_delegates(file_path, methods_to_extract, dest_file):
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()

    tree = ast.parse(source)
    
    class_node = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'ShotReviewTab':
            class_node = node
            break

    if not class_node:
        print("Class not found")
        return

    extracted_functions = []
    replacements = []

    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name in methods_to_extract:
            # Extract source
            start_lineno = node.lineno - 1
            # Handle decorators
            if node.decorator_list:
                start_lineno = node.decorator_list[0].lineno - 1
            
            end_lineno = node.end_lineno
            
            func_lines = source.split('\n')[start_lineno:end_lineno]
            original_code = '\n'.join(func_lines)
            
            # Rewrite to module function
            # def method(self, arg1): -> def method(tab, arg1):
            # and replace self. with tab.
            
            # Find signature
            sig_match = re.search(r'def\s+' + node.name + r'\s*\((.*?)\)', original_code, re.DOTALL)
            if sig_match:
                args = sig_match.group(1).strip()
                new_args = re.sub(r'\bself\b', 'tab', args)
                if new_args == args and 'self' not in args:
                   new_args = 'tab' if not args else 'tab, ' + args
                
                body_code = original_code[sig_match.end():]
                # Replace 'self.' with 'tab.'
                # but careful with strings, we just do a naive replace for now
                # since it's a known file
                body_code = re.sub(r'\bself\b', 'tab', body_code)
                
                new_func_code = f"def {node.name}({new_args}){body_code}"
                
                # De-indent by 4 spaces
                deindented = []
                for line in new_func_code.split('\n'):
                    if line.startswith('    '):
                        deindented.append(line[4:])
                    else:
                        deindented.append(line)
                
                extracted_functions.append('\n'.join(deindented))
                
                # Create replacement delegate for class
                delegate_call_args = []
                for arg in node.args.args:
                    if arg.arg == 'self':
                        delegate_call_args.append('self')
                    else:
                        delegate_call_args.append(arg.arg)
                
                call_args_str = ', '.join(delegate_call_args)
                delegate_code = f"    def {node.name}({args}):\n        return {node.name}({call_args_str})"
                
                replacements.append((original_code, delegate_code))

    with open(dest_file, 'w', encoding='utf-8') as f:
        f.write('from PySide6.QtWidgets import QMessageBox, QProgressDialog\n')
        f.write('from PySide6.QtCore import Qt\n')
        f.write('from .workers import FrameCacheWorker\n')
        f.write('import logging\n')
        f.write('logger = logging.getLogger(__name__)\n\n')
        f.write('\n\n'.join(extracted_functions))
        f.write('\n')

    # Apply replacements
    for orig, new in replacements:
        source = source.replace(orig, new)

    # Insert imports at top of class
    import_stmt = f"\n    from .shot_review.controllers.cache_controller import ({', '.join(methods_to_extract)})\n"
    
    # Just put it under the class def
    source = re.sub(r'(class ShotReviewTab\(QWidget\):.*?)\n', r'\1\n' + import_stmt, source, count=1, flags=re.DOTALL)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(source)

methods = [
    'cache_all_frames',
    '_cancel_cache_worker',
    '_on_cache_progress',
    '_on_cache_finished',
    'clear_cache',
    'update_cache_stats'
]
extract_methods_to_delegates('d:/Soft/UTCAP/V0040/ut_vfx/gui/tabs/shot_review_tab.py', methods, 'd:/Soft/UTCAP/V0040/ut_vfx/gui/tabs/shot_review/controllers/cache_controller.py')
