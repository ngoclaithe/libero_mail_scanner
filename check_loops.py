import ast
import os

def find_loop_depth(node):
    max_d = 0
    for child in ast.iter_child_nodes(node):
        d = find_loop_depth(child)
        if isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
            d += 1
        max_d = max(max_d, d)
    return max_d

for root, _, files in os.walk('backend'):
    for file in files:
        if file.endswith('.py') and 'venv' not in root and '__pycache__' not in root:
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                try:
                    tree = ast.parse(f.read(), filename=path)
                except SyntaxError:
                    continue
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    depth = find_loop_depth(node)
                    if depth > 1:
                        print(f'{path} -> {node.name}(): Độ sâu = {depth}')
