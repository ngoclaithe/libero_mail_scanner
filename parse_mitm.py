import re
import sys

def main():
    filepath = sys.argv[1] if len(sys.argv) > 1 else "log6.txt"
    
    with open(filepath, "rb") as f:
        raw = f.read()
    
    text = raw.decode("utf-8", errors="replace")
    
    print("=" * 70)
    print("1. ALL REQUESTS TO login.libero.it")
    print("=" * 70)
    
    for m in re.finditer(r'method;(\d+):(\w+).{0,500}?host;(\d+):(login\.libero\.it)', text, re.DOTALL):
        method = m.group(2)
        local_start = max(0, m.start() - 500)
        local_end = m.end() + 100
        local = text[local_start:local_end]
        path_m = re.search(r'path;(\d+):(/[^,;]+)', local)
        path = path_m.group(2) if path_m else "?"
        print(f"  {method} https://login.libero.it{path}  (offset={m.start()})")
    
    print()
    print("=" * 70)
    print("2. KEYCHECK.PHP RESPONSE (password POST)")
    print("=" * 70)
    
    kc_pos = text.find("path;13:/keycheck.php")
    if kc_pos < 0:
        kc_pos = text.find("keycheck.php")
    
    if kc_pos > 0:
        print(f"  keycheck.php found at offset {kc_pos}")
        
        search_start = max(0, kc_pos - 10000)
        before = text[search_start:kc_pos]
        
        resp_positions = [m.start() for m in re.finditer(r'8:response;', before)]
        
        if resp_positions:
            resp_pos = search_start + resp_positions[-1]
            resp_block = text[resp_pos:resp_pos + 5000]
            resp_clean = ''.join(c if c.isprintable() else ' ' for c in resp_block)
            
            sc = re.search(r'status_code;(\d+):(\d+)', resp_clean)
            if sc:
                print(f"  Status Code: {sc.group(2)}")
            
            reason = re.search(r'reason;(\d+):(\w+)', resp_clean)
            if reason:
                print(f"  Reason: {reason.group(2)}")
            
            loc = re.search(r'Location,(\d+):([^,\]]+)', resp_clean)
            if loc:
                print(f"  Location: {loc.group(2)}")
            else:
                print("  Location: NOT FOUND")
            
            print("\n  Response Headers:")
            for hm in re.finditer(r'(\d+):([A-Za-z-]+),(\d+):([^,\]]{1,500})', resp_clean):
                name = hm.group(2)
                value = hm.group(4)
                if name.lower() in ('location', 'set-cookie', 'content-type', 
                                     'content-length', 'cache-control'):
                    print(f"    {name}: {value[:200]}")
            
            print(f"\n  Raw response block (first 1500 chars):")
            print(f"  {resp_clean[:1500]}")
        else:
            print("  Could not find response block before keycheck.php")
    else:
        print("  keycheck.php not found in log!")
    
    print()
    print("=" * 70)
    print("3. REQUESTS AFTER KEYCHECK.PHP (next 20 requests)")
    print("=" * 70)
    
    if kc_pos > 0:
        after = text[kc_pos:kc_pos + 50000]
        
        requests_found = []
        for m in re.finditer(r'path;(\d+):(/[^,;]+)', after):
            path = m.group(2)
            local = after[m.start():m.start()+500]
            host_m = re.search(r'host;(\d+):([^;,]+)', local)
            host = host_m.group(2) if host_m else "?"
            method_m = re.search(r'method;(\d+):(\w+)', local)
            method = method_m.group(2) if method_m else "?"
            requests_found.append((method, host, path))
        
        for i, (method, host, path) in enumerate(requests_found[:20]):
            marker = " ***" if "libero" in host else ""
            print(f"  {i+1}. {method} https://{host}{path}{marker}")
    
    print()
    print("=" * 70)
    print("4. ALL appsuite/api REQUESTS")
    print("=" * 70)
    
    for m in re.finditer(r'path;\d+:(/appsuite/api/[^,;]+)', text):
        path = m.group(1)
        local = text[m.start():m.start()+500]
        host_m = re.search(r'host;\d+:([^;,]+)', local)
        host = host_m.group(1) if host_m else "?"
        method_m = re.search(r'method;\d+:(\w+)', local)
        method = method_m.group(1) if method_m else "?"
        
        resp_start = max(0, m.start() - 3000)
        resp_block = text[resp_start:m.start()]
        sc_m = re.findall(r'status_code;\d+:(\d+)', resp_block)
        status = sc_m[-1] if sc_m else "?"
        
        print(f"  {method} https://{host}{path}  (status={status})")
    
    print()
    print("=" * 70)
    print("5. SESSION TOKENS IN URLs/RESPONSES")
    print("=" * 70)
    
    for m in re.finditer(r'session=([a-zA-Z0-9._-]{10,100})', text):
        pos = m.start()
        ctx = text[max(0,pos-100):pos]
        ctx = ''.join(c if c.isprintable() else '' for c in ctx)
        print(f"  session={m.group(1)[:40]}...  context=...{ctx[-60:]}")

if __name__ == "__main__":
    main()
