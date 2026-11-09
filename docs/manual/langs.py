"""The manual in other languages: body_<lang>.html holds the text, navod.html (the Czech manual) the page style. This writes
navod_<lang>.html next to them for each, with the big screenshots as JPEG when tojpg.py has made them, ready for Chrome to print:

    python langs.py                       # every body_*.html
    chrome --headless=new --no-pdf-header-footer --print-to-pdf=JKrender_manual_en.pdf navod_en.html
"""
import glob
import os
import re
import sys

TITLES = {"en": "JKrender – user guide", "de": "JKrender – Anleitung", "ru": "JKrender – руководство",
          "es": "JKrender – guía de uso", "pl": "JKrender – instrukcja"}
here = os.path.dirname(os.path.abspath(__file__))
cs = open(os.path.join(here, "navod.html"), encoding="utf-8").read()
head = cs[:cs.index("<body>") + len("<body>")]
langs = sys.argv[1:] or sorted(re.match(r"body_(\w+)\.html", os.path.basename(p)).group(1)
                               for p in glob.glob(os.path.join(here, "body_*.html")))
for lang in langs:
    body = open(os.path.join(here, "body_%s.html" % lang), encoding="utf-8").read()
    html = head.replace('<html lang="cs">', '<html lang="%s">' % lang)
    html = re.sub(r"<title>.*?</title>", "<title>%s</title>" % TITLES.get(lang, "JKrender"), html)
    html += "\n" + body + "\n</body>\n</html>\n"
    for name in set(re.findall(r"shots/(\w+)\.png", html)):
        if os.path.isfile(os.path.join(here, "shots", name + ".jpg")):
            html = html.replace("shots/%s.png" % name, "shots/%s.jpg" % name)
    open(os.path.join(here, "navod_%s.html" % lang), "w", encoding="utf-8").write(html)
    print("navod_%s.html" % lang)
