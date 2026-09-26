import re

html = open(r"C:\Users\Administrator\AppData\Local\Temp\sr.html", encoding="utf-8", errors="replace").read()
print("page size:", len(html))
emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", html)
print("emails found:", len(emails), emails[:5])
m = re.findall(r"([\d,]{4,})\s*(?:results|total|rows|matches|credentials)", html, re.I)
print("result counts:", m[:8])
print("contains 5,080,949:", "5,080,949" in html)
