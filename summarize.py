import os
import sys
from bs4 import BeautifulSoup

keys=['passed', 'failed', 'skipped', 'errors', 'xfailed']
totals={}

header = '''| Filename | Passed | Failed | Skipped | Errors | Expected |
|-----|-----|-----|-----|-----|-----|
'''

def get_value(html, key):
	spans = html.find_all('span', {'class':key})
	if len(spans) == 0:
		return 0
	return int(spans[0].text.split()[0])
	
def run(path):
	passed = 0
	failed = 0
	skipped = 0
	errors = 0
	xfailed = 0
		
	# print("| Filename | Passed | Failed | Skipped | Errors | Expected |")
	# print("|-----|-----|-----|-----|-----|-----|")
	print(header)
	for filename in os.listdir(path):
		if filename.endswith(".html"):
			with open(filename) as f:				
				html = BeautifulSoup(f, features='lxml')
				p = get_value(html, 'passed')
				f = get_value(html, 'failed')
				e = get_value(html, 'errors')
				x = get_value(html, 'xfailed')
				s = get_value(html, 'skipped')
				print(f"| {filename} | {p} | {f} | {s} | {e} | {x} |")
				passed += p
				failed += f
				xfailed += x
				skipped += s
				errors += e
	print()
	print("| Status | Count |")
	print("|-----|-----|")
	print(f"| Passed | { passed} |")
	print(f"| Failed | { failed} |")
	print(f"| Skipped | { skipped} |")
	print(f"| Errors | { errors} |")
	print(f"| Expected | { xfailed } |")
	
if __name__ == "__main__":
	run(sys.argv[1])