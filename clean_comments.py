import os
import re

def clean_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    orig = content
    
    if path.endswith('.py'):
        content = re.sub(r'^[ \t]*\n?', '', content, flags=re.MULTILINE)
        content = re.sub(r"^[ \t]*'''[\s\S]*?'''\n?", '', content, flags=re.MULTILINE)
        content = re.sub(r'', '', content)
        content = re.sub(r'^[ \t]*#[^\n]*\n?', '', content, flags=re.MULTILINE)
        content = re.sub(r'[ \t]+#[^\n]*', '', content)
        
    elif path.endswith(('.js', '.jsx', '.ts', '.tsx')):
        content = re.sub(r'\{[ \t]*/\*[\s\S]*?\*/[ \t]*\}\n?', '', content)
        content = re.sub(r'^[ \t]*/\*[\s\S]*?\*/\n?', '', content, flags=re.MULTILINE)
        content = re.sub(r'/\*[\s\S]*?\*/', '', content)
        content = re.sub(r'^[ \t]*//[^\n]*\n?', '', content, flags=re.MULTILINE)
        content = re.sub(r'[ \t]+//[^\n]*', '', content)

    content = re.sub(r'\n{3,}', '\n\n', content)

    if content != orig:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Cleaned {path}")

for root, dirs, files in os.walk(r'd:\libero_mail'):
    if any(x in root for x in ['node_modules', '.git', '.venv', '__pycache__', 'dist', 'build']):
        continue
    for file in files:
        if file.endswith(('.py', '.js', '.jsx')):
            clean_file(os.path.join(root, file))
