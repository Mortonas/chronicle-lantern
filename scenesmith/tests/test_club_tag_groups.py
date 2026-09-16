from copy import deepcopy

from core.club_prep import capture_document


def test_forty_guests_form_stable_groups_of_four():
    from core.club_tag_groups import tag_arrangement
    docs = [capture_document(str(i), f'Guest {i:02}', '#court #politics #npc') for i in range(40)]
    rows = tag_arrangement(docs, '')
    assert len(rows) == 10
    assert all(len(r['members']) == 4 for r in rows)
    assert rows == tag_arrangement(list(reversed(docs)), '')
    assert sorted(n for r in rows for n in r['members']) == sorted(d.npc_id for d in docs)
    assert [r['number'] for r in rows] == list(range(1, 11))


def test_stronger_matches_win_and_late_arrival_stays_last():
    from core.club_tag_groups import tag_arrangement
    docs = [capture_document(n, n, tags) for n, tags in [('a','#court #art'),('b','#court #art'),
            ('c','#court #art'),('d','#court #art'),('e','#court'),('f','#music'),('g','#court #art')]]
    rows = tag_arrangement(docs, 'g')
    assert rows[0]['members'] == ['a','b','c','d']
    assert rows[-1]['members'] == ['g']
    assert rows[-1]['availability'] == 'expected'
    assert rows[1]['members'] == ['e'] and rows[2]['members'] == ['f']


def test_yaml_nested_tags_and_generic_tags():
    from core.club_tag_groups import tag_arrangement
    docs = [capture_document('a','Ada','---\ntags: [Court/Politics, art]\n---\n#npc'),
            capture_document('b','Bea','#court/politics #ART #npc'),
            capture_document('c','Cy','#npc')]
    rows = tag_arrangement(docs, '')
    assert rows[0]['members'] == ['a','b']
    assert rows[1]['members'] == ['c']
    assert 'Politics' in rows[0]['label']


def test_sheet_tags_masks_complete_links_without_masking_visible_or_adjacent_tags():
    from core.club_tag_groups import sheet_tags

    document = capture_document(
        'ada',
        'Ada',
        '''See #Riverton in [this note](#At-a-Glance).
[See #Council](#At-a-Glance "section") #Court
[See #Executives](#At-a-Glance 'section')
[See #Artists](#At-a-Glance (section))
[See #Independent](<#At-a-Glance>)#Politics
[See #Dockworkers](<#At-a-Glance> "section")
[See #Nested [detail]](#At-a-Glance(part)) #AfterNested
![#ImageLabel](<#ImageHeading> 'section') #AfterImage
[[#At a Glance|#HiddenAlias]] #Visible
![[Somewhere#Disciplines|#AlsoHidden]]
\\[[#EscapedWiki]]
''',
    )

    assert sheet_tags(document) == {
        'independent',
        'afterimage',
        'afternested',
        'dockworkers',
        'council',
        'riverton',
        'court',
        'escapedwiki',
        'imagelabel',
        'nested',
        'politics',
        'artists',
        'executives',
        'visible',
    }


def test_sheet_tags_leave_incomplete_or_multiline_link_syntax_as_ordinary_prose():
    from core.club_tag_groups import sheet_tags

    document = capture_document(
        'ada',
        'Ada',
        '''[[#Incomplete
[Label](#Unclosed
[Label](<#UnclosedAngle
[[#AcrossWiki
Line]]
[Label](#AcrossMarkdown
Line)
[See #VisibleLabel](note.md "#NonAnchorTitle")
[See #ReferenceLabel][#ReferenceTarget]
''',
    )

    assert sheet_tags(document) == {
        'acrossmarkdown',
        'acrosswiki',
        'incomplete',
        'nonanchortitle',
        'referencelabel',
        'referencetarget',
        'unclosed',
        'unclosedangle',
        'visiblelabel',
    }


def test_false_link_anchors_do_not_form_groups_but_real_shared_tags_do():
    from core.club_tag_groups import tag_arrangement

    false_only = [
        capture_document('a', 'Ada', '[[#At a Glance]] [Motives](#Plots)'),
        capture_document('b', 'Bea', '[[#At a Glance]] [Motives](<#Plots>)'),
    ]
    assert [row['members'] for row in tag_arrangement(false_only, '')] == [['a'], ['b']]

    shared = [
        capture_document('a', 'Ada', '[[#At a Glance]] #court'),
        capture_document('b', 'Bea', '[Motives](#Plots) #court'),
    ]
    assert [row['members'] for row in tag_arrangement(shared, '')] == [['a', 'b']]


def test_groups_exist_without_ai_and_expand_without_provider(tmp_path, qapp, qtbot, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication, QToolTip
    from core.club_generation import ClubGenerationService
    from test_club_prep import SceneProvider
    from ui.club_tab import ClubTab
    paths=[]
    for i in range(40):
        path=tmp_path/f'Guest {i:02}.md'
        path.write_text(f'# Guest {i:02}\n\n#court #politics\n',encoding='utf-8')
        paths.append(str(path))
    provider=SceneProvider()
    service=ClubGenerationService({},cache_root=tmp_path/'cache',provider=provider)
    result=service.build_event_result(paths,seed=4,use_ai=False)
    rows=result.event.dashboard['scene_prep']['encounters']
    assert len(rows)==11  # Ten groups plus reserved late arrival.
    assert max(len(r['members']) for r in rows)==4
    original=deepcopy(result.event.dashboard)
    tab=ClubTab(service)
    qtbot.addWidget(tab)
    tab._on_event_ready(result)
    text=tab.summaryBrowser.toPlainText()
    assert '4 guests' in text
    assert 'group:' in tab.summaryBrowser.toHtml()
    previews=[]
    monkeypatch.setattr(QToolTip, 'showText', lambda point, text, widget: previews.append(text))
    tab.summaryBrowser.highlighted.emit(QUrl('group:1'))
    assert all(tab._name_for(n) in previews[-1] for n in rows[0]['members'])
    tab.summaryBrowser.anchorClicked.emit(QUrl('group:1'))
    assert all(tab._name_for(n) in tab.drawer.toPlainText() for n in rows[0]['members'])
    assert 'npc:' in tab.drawer.toHtml()
    tab.copyTablePrepBtn.click()
    copied=QApplication.clipboard().text()
    assert all(f'Guest {i:02}' in copied for i in range(40))
    assert result.event.dashboard==original
    assert not provider.calls


def test_tag_arrangement_survives_authentication_failure(tmp_path):
    from core.club_generation import ClubGenerationService
    class Failed:
        calls=0
        def generate_from_messages(self, *args, **kwargs):
            self.calls+=1
            raise RuntimeError('invalid api key')
    paths=[]
    for i in range(6):
        path=tmp_path/f'Person {i}.md'
        path.write_text(f'# Person {i}\n\n#politics #court',encoding='utf-8')
        paths.append(str(path))
    provider=Failed()
    service=ClubGenerationService({},cache_root=tmp_path/'cache',provider=provider)
    baseline=service.build_event_result(paths,seed=1,use_ai=False)
    result=service.build_event_result(paths,seed=1)
    assert len(result.event.attendee_ids)==6
    assert result.event.dashboard['scene_prep']['encounters']==baseline.event.dashboard['scene_prep']['encounters']
    assert result.event.metadata['generation_mode']=='deterministic_fallback'
    assert provider.calls==1


def test_cached_tag_groups_late_arrival_and_sheet_changes(tmp_path):
    import json
    from core.club_generation import ClubGenerationService
    from core.club_prep import npc_scene_context
    from test_club_prep import SceneProvider
    paths=[]
    for i in range(6):
        path=tmp_path/f'Person {i}.md'
        path.write_text(f'# Person {i}\n\nPublicly enjoys conversation.\n#hidden-allegiance #court',encoding='utf-8')
        paths.append(str(path))
    provider=SceneProvider()
    service=ClubGenerationService({},cache_root=tmp_path/'cache',provider=provider)
    first=service.build_event_result(paths,seed=1)
    count=len(provider.calls)
    second=service.build_event_result(paths,seed=1)
    assert len(provider.calls)==count
    assert first.event.dashboard['scene_prep']==second.event.dashboard['scene_prep']
    assert 'hidden-allegiance' not in json.dumps(npc_scene_context(first.event.dashboard['scene_prep']))
    finals=[p for p in provider.calls if 'sections' in p and 'evidence' in p]
    assert all('encounters' not in p['sections'] for p in finals)
    assert all('hidden-allegiance' not in json.dumps(p['accepted_sections']) for p in finals)
    late=next(n for n in first.event.attendee_ids if n!=first.event.late_arrival_id)
    changed=service.complete_event_prep(first,late_arrival_id=late)
    assert changed.event.seed==first.event.seed
    assert changed.event.dashboard['rumors_in_circulation']==first.event.dashboard['rumors_in_circulation']
    assert changed.event.dashboard['scene_prep']['encounters'][-1]['members']==[late]
    from pathlib import Path
    Path(paths[0]).write_text('#different-topic',encoding='utf-8')
    revised=service.build_event_result(paths,seed=1)
    assert revised.event.dashboard['scene_prep']['revision']!=first.event.dashboard['scene_prep']['revision']
