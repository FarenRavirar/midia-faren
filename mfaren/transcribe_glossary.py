import re


def _compile_pattern(source):
    source = str(source or "").strip()
    if not source:
        return None
    escaped = re.escape(source)
    start_boundary = bool(re.match(r"^\w", source, flags=re.UNICODE))
    end_boundary = bool(re.search(r"\w$", source, flags=re.UNICODE))
    pattern = f"{r'\b' if start_boundary else ''}{escaped}{r'\b' if end_boundary else ''}"
    return re.compile(pattern, flags=re.IGNORECASE)


def _iter_lines(raw):
    if isinstance(raw, list):
        for item in raw:
            yield str(item or "")
        return
    for line in str(raw or "").splitlines():
        yield line


def parse_glossary(raw):
    rules = []
    for line in _iter_lines(raw):
        text = str(line or "").strip()
        if not text or text.startswith("#"):
            continue
        match = re.match(r"^(.+?)(=>|->)(.+)$", text)
        if not match:
            continue
        source = str(match.group(1) or "").strip()
        target = str(match.group(3) or "").strip()
        if not source or not target:
            continue
        pattern = _compile_pattern(source)
        if not pattern:
            continue
        rules.append({"source": source, "target": target, "pattern": pattern})
    return rules


def parse_known_terms(raw, glossary_rules=None):
    terms = []
    seen = set()

    def _add(term):
        name = str(term or "").strip()
        if not name:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(name)

    for line in _iter_lines(raw):
        text = str(line or "").strip()
        if not text or text.startswith("#"):
            continue
        match = re.match(r"^(.+?)(=>|->)(.+)$", text)
        if match:
            _add(match.group(3))
            continue
        _add(text)

    for rule in list(glossary_rules or []):
        _add(rule.get("target"))

    return terms


def build_guidance_prompt(known_terms, max_terms=80):
    terms = [str(t).strip() for t in list(known_terms or []) if str(t).strip()]
    if not terms:
        return ""
    picked = terms[: max(1, int(max_terms))]
    joined = ", ".join(picked)
    return f"Termos conhecidos deste projeto: {joined}."


def apply_glossary(text, rules):
    out = str(text or "")
    if not out or not rules:
        return out
    for rule in rules:
        pattern = rule.get("pattern")
        target = rule.get("target")
        if not pattern or target is None:
            continue
        out = pattern.sub(str(target), out)
    return out
