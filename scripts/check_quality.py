"""Quick quality check of generated text."""
import re
import sys

with open(r"e:\VibeProjects\Autor24\scripts\test_gen_output.txt", "r", encoding="utf-8") as f:
    text = f.read()

text_lower = text.lower()
banned = [
    "таким образом", "стоит отметить", "стоит подчеркнуть",
    "необходимо отметить", "следует подчеркнуть", "следует отметить",
    "не менее важным является", "особое внимание следует уделить",
    "важно понимать, что", "нельзя не отметить", "подводя итоги",
    "на основании вышеизложенного", "представляется целесообразным",
    "в данном контексте", "в свою очередь", "в связи с этим",
    "в заключение следует отметить", "в заключение можно сказать",
    "в заключение можно отметить", "исследования показывают",
    "по данным исследований", "как показывают исследования",
]

chars = len(text)
words = len(text.split())
pages = chars // 1800
print(f"Chars: {chars}, Words: {words}, Pages: ~{pages}")

found = 0
for phrase in banned:
    cnt = text_lower.count(phrase)
    if cnt > 0:
        found += cnt
        # Find context
        idx = text_lower.find(phrase)
        context = text[max(0,idx-30):idx+len(phrase)+30].replace("\n", " ")
        print(f"  BANNED: [{phrase}] x{cnt} -- ...{context}...")
print(f"Banned phrases total: {found}")

# Percentages without author
pct_no_author = 0
for sent in re.split(r"[.!?]", text):
    if re.search(r"\d+\s*%", sent) and not re.search(r"[A-Z\u0410-\u042f\u0401][a-z\u0430-\u044f\u0451]+\s*\(\d{4}\)", sent):
        pct_no_author += 1
        if pct_no_author <= 3:
            s = sent.strip()[:100].encode("ascii", "replace").decode("ascii")
            print(f"  PCT: {s}")
print(f"Percentages without author: {pct_no_author}")

# Markdown
md = len(re.findall(r"[#*>`]", text))
print(f"Markdown artifacts: {md}")

# Structure
has_intro = "ВВЕДЕНИЕ" in text or "Введение" in text
has_concl = "ЗАКЛЮЧЕНИЕ" in text or "Заключение" in text
has_bib = "СПИСОК ЛИТЕРАТУРЫ" in text or "Список литературы" in text
chapters = re.findall(r"^(Глава\s+\d+|ГЛАВА\s+\d+)", text, re.MULTILINE)
subsections = re.findall(r"^\d+\.\d+\.?\s+[A-ZА-ЯЁ]", text, re.MULTILINE)
print(f"Intro: {has_intro}, Conclusion: {has_concl}, Bibliography: {has_bib}")
print(f"Chapters: {len(chapters)}, Subsections: {len(subsections)}")

# Bib entries
bib_start = text.find("СПИСОК ЛИТЕРАТУРЫ")
if bib_start < 0:
    bib_start = text.find("Список литературы")
if bib_start >= 0:
    bib_text = text[bib_start:]
    entries = re.findall(r"^\d+\.", bib_text, re.MULTILINE)
    print(f"Bibliography entries: {len(entries)}")

# Intro length
intro_start = text.find("ВВЕДЕНИЕ")
if intro_start < 0:
    intro_start = text.find("Введение")
if intro_start >= 0:
    chap1 = re.search(r"(Глава\s+1|ГЛАВА\s+1)", text[intro_start+10:])
    if chap1:
        intro_text = text[intro_start:intro_start+10+chap1.start()]
        print(f"Intro words: {len(intro_text.split())}")

# Conclusion length
conc_start = text.find("ЗАКЛЮЧЕНИЕ")
if conc_start < 0:
    conc_start = text.find("Заключение")
if conc_start >= 0 and bib_start > conc_start:
    conc_text = text[conc_start:bib_start]
    print(f"Conclusion words: {len(conc_text.split())}")

# Unique authors cited vs in bibliography
content_text = text[:bib_start] if bib_start > 0 else text
cited_ru = set(re.findall(r"([А-ЯЁ][а-яё]{2,})\s*\(\d{4}\)", content_text))
cited_en = set(re.findall(r"([A-Z][a-z]{2,})\s*\(\d{4}\)", content_text))
print(f"Unique authors cited: {len(cited_ru)} RU + {len(cited_en)} EN = {len(cited_ru)+len(cited_en)}")

if bib_start >= 0:
    bib_lower = text[bib_start:].lower()
    missing_ru = [a for a in cited_ru if a.lower()[:4] not in bib_lower]
    missing_en = [a for a in cited_en if a.lower() not in bib_lower]
    if missing_ru:
        print(f"  MISSING in bib (RU): {', '.join(sorted(missing_ru)[:10])}")
    if missing_en:
        print(f"  MISSING in bib (EN): {', '.join(sorted(missing_en)[:10])}")
    total_missing = len(missing_ru) + len(missing_en)
    print(f"  Total missing: {total_missing}")
else:
    print("  No bibliography found!")

# Check intro methods (empirical methods that shouldn't be there)
if intro_start >= 0 and chap1:
    intro_lower = text[intro_start:intro_start+10+chap1.start()].lower()
    empirical = ["анкетирование", "наблюдение за образовательным", "метод экспертных оценок"]
    found_emp = [m for m in empirical if m in intro_lower]
    if found_emp:
        print(f"  WARNING: Empirical methods in intro: {', '.join(found_emp)}")
    else:
        print("  OK: No empirical methods in intro")

print("\nDONE")
