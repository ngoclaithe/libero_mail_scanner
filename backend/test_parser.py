def parse_imap_list(s: str):
    stack = [[]]
    token = ""
    in_str = False
    escape = False
    i = 0
    while i < len(s):
        char = s[i]
        if escape:
            token += char
            escape = False
        elif char == '\\':
            escape = True
        elif char == '"':
            in_str = not in_str
        elif in_str:
            token += char
        elif char == '(':
            stack.append([])
        elif char == ')':
            if token:
                stack[-1].append(token)
                token = ""
            sub = stack.pop()
            stack[-1].append(sub)
        elif char == ' ':
            if token:
                stack[-1].append(token)
                token = ""
        else:
            token += char
        i += 1
    if token: stack[-1].append(token)
    return stack[0][0] if stack and stack[0] else []

def find_parts(parsed, prefix="") -> list:
    parts = []
    if isinstance(parsed, list) and len(parsed) > 0:
        if isinstance(parsed[0], list): # Multipart
            # Last element is usually "MIXED" or "RELATED"
            subparts = [p for p in parsed if isinstance(p, list)]
            for i, p in enumerate(subparts):
                num = f"{prefix}.{i+1}" if prefix else str(i+1)
                parts.extend(find_parts(p, num))
        elif isinstance(parsed[0], str): # Single part
            mime1 = parsed[0].lower()
            mime2 = parsed[1].lower() if len(parsed) > 1 and isinstance(parsed[1], str) else ""
            mime = f"{mime1}/{mime2}"
            num = prefix if prefix else "1"
            parts.append((num, mime))
    return parts

bs = '((("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 12 1)("image" "jpeg" ("name" "foo.jpg") "<id>" "Attachment" "base64" 300) "mixed")("application" "pdf" ("name" "bar.pdf") "<id>" "attachment" "base64" 500) "mixed")'

parsed = parse_imap_list(bs)
print(parsed)
print(find_parts(parsed))

