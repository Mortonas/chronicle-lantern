"""Local tag similarity for Club staging; never establishes shared knowledge."""
from collections import Counter
from fractions import Fraction
import re
import unicodedata

import yaml


GENERIC_TAGS = {"npc", "npcs", "character", "characters"}


def _is_escaped(text, index):
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == '\\':
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _complete_wikilink_spans(text):
    spans = []
    index = 0
    while index < len(text) - 1:
        if text.startswith('[[', index) and not _is_escaped(text, index):
            cursor = index + 2
            while cursor < len(text) - 1 and text[cursor] not in '\r\n':
                if text.startswith(']]', cursor) and not _is_escaped(text, cursor):
                    spans.append((index, cursor + 2))
                    index = cursor + 2
                    break
                cursor += 1
            else:
                index += 2
            continue
        index += 1
    return spans


def _balanced_label_end(text, start):
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character in '\r\n':
            return None
        if character == '\\' and index + 1 < len(text):
            index += 2
            continue
        if character == '[':
            depth += 1
        elif character == ']':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _quoted_title_end(text, start, quote):
    index = start + 1
    while index < len(text):
        character = text[index]
        if character in '\r\n':
            return None
        if character == '\\' and index + 1 < len(text):
            index += 2
            continue
        if character == quote:
            return index + 1
        index += 1
    return None


def _parenthesized_title_end(text, start):
    depth = 1
    index = start + 1
    while index < len(text):
        character = text[index]
        if character in '\r\n':
            return None
        if character == '\\' and index + 1 < len(text):
            index += 2
            continue
        if character == '(':
            depth += 1
        elif character == ')':
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _anchor_suffix_end(text, start):
    index = start + 1
    while index < len(text) and text[index] in ' \t':
        index += 1
    if index >= len(text) or text[index] in '\r\n':
        return None

    if text[index] == '<':
        index += 1
        if index >= len(text) or text[index] != '#':
            return None
        index += 1
        while index < len(text):
            character = text[index]
            if character in '\r\n' or character == '<':
                return None
            if character == '\\' and index + 1 < len(text):
                index += 2
                continue
            if character == '>':
                index += 1
                break
            index += 1
        else:
            return None
    elif text[index] == '#':
        index += 1
        depth = 0
        while index < len(text):
            character = text[index]
            if character in '\r\n':
                return None
            if character == '\\' and index + 1 < len(text):
                index += 2
                continue
            if character in ' \t' and depth == 0:
                break
            if character == '(':
                depth += 1
            elif character == ')':
                if depth == 0:
                    break
                depth -= 1
            index += 1
        if depth:
            return None
    else:
        return None

    had_separator = False
    while index < len(text) and text[index] in ' \t':
        had_separator = True
        index += 1
    if index < len(text) and text[index] == ')':
        return index + 1
    if not had_separator or index >= len(text):
        return None

    if text[index] in {'"', "'"}:
        index = _quoted_title_end(text, index, text[index])
    elif text[index] == '(':
        index = _parenthesized_title_end(text, index)
    else:
        return None
    if index is None:
        return None
    while index < len(text) and text[index] in ' \t':
        index += 1
    return index + 1 if index < len(text) and text[index] == ')' else None


def _mask_link_syntax(text):
    masked = list(text)
    for start, end in _complete_wikilink_spans(text):
        masked[start:end] = ' ' * (end - start)

    scan_text = ''.join(masked)
    index = 0
    while index < len(scan_text):
        if scan_text[index] != '[' or _is_escaped(scan_text, index):
            index += 1
            continue
        label_end = _balanced_label_end(scan_text, index)
        suffix_start = label_end + 1 if label_end is not None else -1
        if suffix_start < len(scan_text) and suffix_start >= 0 and scan_text[suffix_start] == '(':
            suffix_end = _anchor_suffix_end(scan_text, suffix_start)
            if suffix_end is not None:
                masked[suffix_start:suffix_end] = ' ' * (suffix_end - suffix_start)
                scan_text = ''.join(masked)
                index = suffix_end
                continue
        index += 1
    return ''.join(masked)


def sheet_tags(document):
    text = ''.join(p.text for p in document.passages)
    values = re.findall(r'(?<![\w/#])#([\w][\w/-]*)', _mask_link_syntax(text))
    front = re.match(r'\A\ufeff?---\s*\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
    if front:
        try:
            data = yaml.safe_load(front.group(1))
            tags = data.get('tags', []) if isinstance(data, dict) else []
            values.extend(tags if isinstance(tags, list) else re.split(r'[,\s]+', tags) if isinstance(tags, str) else [])
        except yaml.YAMLError:
            pass  # Inline tags remain usable when frontmatter is malformed.
    normalized = {unicodedata.normalize('NFKC', v).casefold().strip().lstrip('#') for v in values if isinstance(v, str)}
    return {t for t in normalized if t and len(t) <= 80 and t not in GENERIC_TAGS}


def tag_arrangement(documents, late):
    from core.club_prep import number_encounters
    names = {d.npc_id: (unicodedata.normalize('NFKC', d.name).casefold(), d.npc_id) for d in documents}
    tags = {d.npc_id: sheet_tags(d) for d in documents}
    clusters = [(n,) for n in sorted(names, key=names.get) if n != late]
    while True:
        best = None
        for i, left in enumerate(clusters):
            for j in range(i + 1, len(clusters)):
                right = clusters[j]
                if len(left) + len(right) > 4:
                    continue
                overlap = sum(len(tags[a] & tags[b]) for a in left for b in right)
                if not overlap:
                    continue
                members = tuple(sorted((*left, *right), key=names.get))
                # Strongest mean cross-group overlap first. Equal matches fill
                # groups before starting more pairs; names/IDs settle every tie.
                key = (-Fraction(overlap, len(left) * len(right)), -len(members), tuple(names[n] for n in members))
                if best is None or key < best[0]:
                    best = (key, i, j, members)
        if best is None:
            break
        _, i, j, members = best
        clusters[i] = members
        del clusters[j]
    if late in names:
        clusters.append((late,))
    rows = []
    for members in clusters:
        common = Counter(t for n in members for t in tags[n])
        shared = sorted((t for t in common if common[t] >= 2), key=lambda t: (-common[t], t))
        label = ' / '.join(t.replace('-', ' ').replace('_', ' ').replace('/', ' · ').title() for t in shared[:2])[:90]
        expected = list(members) == [late]
        group = len(members) > 1
        rows.append({"members": list(members), "label": label if group else names[members[0]][0],
                     "cue": "Not here yet—reroll until arrival." if expected else "A small conversation is forming." if group else "No matching tag group; available for an individual encounter.",
                     "reason": ("Matching tags: " + ', '.join(shared[:4]) + '.') if group else '',
                     "member_reasons": [], "evidence": [], "basis": "tag_similarity",
                     "availability": "expected" if expected else "present"})
    return number_encounters(rows, documents, late)
