import re

html = open(r"C:\Users\Administrator\AppData\Local\Temp\sr.html", encoding="utf-8", errors="replace").read()
# find the region around the result count
i = html.find("1756655")
print("---- context around count ----")
print(html[max(0, i - 400):i + 400])
# table rows
tr = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
print("---- table rows:", len(tr))
for r in tr[1:4]:
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
    print([re.sub(r"<[^>]+>", "", c).strip()[:40] for c in cells])
