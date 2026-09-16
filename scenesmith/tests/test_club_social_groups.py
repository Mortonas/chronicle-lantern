import json
from copy import deepcopy

import pytest

from core.club_cache import ClubCacheService
from core.club_prep import PrepEngine, PrepError, RequestBudget, capture_document, validate_scene_section, scene_display_sections
from test_club_prep import SceneProvider, documents


def group_rows(docs):
    evidence = {p.passage_id: p for d in docs for p in d.passages}
    own = {p.npc_id: p.passage_id for p in evidence.values() if p.text.strip() and not p.text.startswith('#')}
    annotations = {p: {'visibility': 'unknown', 'categories': ['social']} for p in evidence}
    row = {'members': ['a', 'b'], 'cue': 'Trading travel stories.', 'reason': 'Both enjoy conversation.',
           'evidence': [own['a'], own['b']], 'member_reasons': [
               {'npc_id': n, 'text': 'Enjoys meeting new people.', 'participation_basis': 'social', 'evidence': [own[n]]}
               for n in ('a', 'b')]}
    return row, evidence, annotations


def test_own_social_temperament_supports_participation_without_secret_transfer():
    docs = tuple(capture_document(n, n.upper(), 'Enjoys conversation and travel stories.') for n in ('a', 'b'))
    row, evidence, annotations = group_rows(docs)
    assert validate_scene_section('encounters', [row], docs, '', evidence, annotations)
    row['member_reasons'][0]['evidence'] = row['evidence']
    with pytest.raises(PrepError, match='private_target_knowledge'):
        validate_scene_section('encounters', [row], docs, '', evidence, annotations)
    row['member_reasons'][0]['evidence'] = [row['evidence'][0]]
    for a in annotations.values():
        a['categories'] = ['motives']
    with pytest.raises(PrepError):
        validate_scene_section('encounters', [row], docs, '', evidence, annotations)


class GroupProvider(SceneProvider):
    broken = True

    def generate_from_messages(self, messages, **kwargs):
        value = json.loads(super().generate_from_messages(messages, **kwargs))
        if 'encounters' in value:
            payload = self.calls[-1]
            own = {p['npc_id']: p['passage_id'] for p in payload['evidence'] if p['text'].strip() and not p['text'].startswith('#')}
            present = sorted(n for n in own if n != payload['late_arrival_id'])
            pair = present[:2]
            refs = [own[n] for n in pair]
            group = {'members': pair, 'cue': 'Discussing animal care.', 'reason': 'Shared interest in animal care.',
                     'evidence': refs, 'member_reasons': [
                         {'npc_id': n, 'text': 'Interested in animal care.', 'evidence': refs} for n in pair]}
            solos = [{'members': [n], 'cue': 'Enjoying a quiet moment.', 'reason': '', 'evidence': ['invalid'] if self.broken else [own[n]],
                      'member_reasons': [{'npc_id': n, 'text': 'Prefers quiet.', 'evidence': [own[n]]}]} for n in present[2:]]
            value['encounters'] = ([] if payload.get('retained_encounters') else [group]) + solos
        return json.dumps(value)


def test_valid_group_survives_rejection_retry_and_cache_resume(tmp_path):
    provider = GroupProvider()
    engine = PrepEngine(ClubCacheService(tmp_path), provider, {})
    context = engine.context(documents(), RequestBudget())
    scene = engine.scene(context, '', {}, {}, RequestBudget())
    assert [r['members'] for r in scene['encounters']] == [['a', 'b'], ['c']]
    assert scene['encounters'][0]['basis'] == 'suggested'
    assert scene['encounters'][1]['basis'] == 'unavailable'
    assert scene['incomplete']
    assert any(p.get('retained_encounters') for p in provider.calls)
    assert 'not a decision' in dict(scene_display_sections(scene, {}))['Social Groups and Loners'][1]
    retained = deepcopy(scene['encounters'][0])
    provider.broken = False
    resumed = engine.scene(context, '', {}, {}, RequestBudget())
    assert not resumed['incomplete']
    assert resumed['encounters'][0] == retained
    count = len(provider.calls)
    assert engine.scene(context, '', {}, {}, RequestBudget()) == resumed
    assert len(provider.calls) == count


def test_scene_reserves_social_evidence_across_roster(tmp_path):
    docs = [capture_document('a', 'Ada', ('Extensive public business history. ' * 2000) + '\n\nEnjoys lively conversation.'),
            capture_document('z', 'Zed', 'Enjoys lively conversation.')]
    engine = PrepEngine(ClubCacheService(tmp_path), SceneProvider(), {})
    context = engine.context(docs, RequestBudget())
    for doc in docs:
        for a in context.readings[doc.npc_id]['annotations']:
            a['categories'] = ['social'] if a['passage_id'] == doc.passages[-1].passage_id else ['power']
    _, evidence = engine._evidence_payload(context, {'candidates': ['a'], 'queries': ['business history'], 'references': []},
                                          {'roster': engine._roster(context), 'late_arrival_id': '', 'sections': ['encounters']}, 'scene_final')
    assert docs[1].passages[-1].passage_id in evidence
    assert docs[0].passages[-1].passage_id in evidence


def test_conflicting_groups_are_rejected_without_losing_unrelated_entries():
    from core.club_prep import admit_encounter_rows
    docs = documents()
    row, evidence, annotations = group_rows(docs)
    for a in annotations.values():
        a.update(visibility='public', categories=['social'])
    c_ref = docs[2].passages[-1].passage_id
    solo = {'members': ['c'], 'cue': 'Enjoying quiet.', 'reason': '', 'evidence': [c_ref],
            'member_reasons': [{'npc_id': 'c', 'text': 'Enjoying quiet.', 'evidence': [c_ref]}]}
    for rows in ([row, deepcopy(row), solo], [solo, deepcopy(row), row]):
        kept, errors = admit_encounter_rows(rows, [], docs, '', evidence, annotations)
        assert [r['members'] for r in kept] == [['c']]
        assert errors == ['duplicate_group_member']


def test_generated_group_renders_and_copies_through_real_controls(tmp_path, qapp, qtbot):
    from PySide6.QtWidgets import QApplication
    from core.club_generation import ClubGenerationService
    from ui.club_tab import ClubTab
    paths = []
    for name in ('Ada', 'Bea', 'Cy'):
        path = tmp_path / (name + '.md')
        path.write_text(f'# {name}\n\nPublicly supports animal care.\n#animal-care\n', encoding='utf-8')
        paths.append(str(path))
    provider = GroupProvider()
    service = ClubGenerationService({}, cache_root=tmp_path / 'cache', vault_root=tmp_path, provider=provider)
    result = service.build_event_result(paths, seed=4)
    scene = result.event.dashboard['scene_prep']
    assert [len(r['members']) for r in scene['encounters']] == [2, 1]
    tab = ClubTab(service)
    qtbot.addWidget(tab)
    tab._on_event_ready(result)
    calls = len(provider.calls)
    assert 'Animal Care' in tab.summaryBrowser.toPlainText()
    tab.copyTablePrepBtn.click()
    copied = QApplication.clipboard().text()
    assert '1. ' in copied and '2. ' in copied and '3. ' not in copied
    assert 'animal-care' in copied and 'Not here yet' in copied
    assert 'member_reasons' not in copied
    assert len(provider.calls) == calls
