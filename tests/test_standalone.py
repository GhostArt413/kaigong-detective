"""Local-edition behavior checks: isolated credentials, deterministic providers, no paid requests."""
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest
from standalone import server as app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'CONFIG', tmp_path / 'config.json')
    monkeypatch.setattr(app, 'DATA', tmp_path / 'sessions')
    monkeypatch.setattr(app.yb, 'KEY_FILE', tmp_path / 'missing.env')
    for name in ('TENCENTCLOUD_WSA_APIKEY', 'SCOUT_LLM_BASE_URL', 'SCOUT_LLM_MODEL', 'SCOUT_LLM_API_KEY'):
        monkeypatch.delenv(name, raising=False)


def base_session():
    return {'id': 'a' * 32, 'goal': '创建一个保留时间点的中文视频笔记工具', 'status': 'waiting',
            'messages': [], 'rounds': [], 'sources': [], 'searches': [], 'events': [], 'error': ''}


def test_configuration_never_returns_secret_and_is_private():
    result = app.save_config({'base_url': 'https://model.example/v1', 'model': 'example', 'llm_key': 'MODEL-SECRET-FIXTURE', 'search_key': 'SEARCH-SECRET-FIXTURE'})
    assert 'SECRET-FIXTURE' not in json.dumps(result)
    assert app.config()['llm_key'] == 'MODEL-SECRET-FIXTURE'
    assert app.CONFIG.stat().st_mode & 0o777 == 0o600


def test_blank_keys_preserve_and_changed_provider_cannot_receive_previous_key():
    app.save_config({'base_url': 'https://a.example/v1', 'model': 'one', 'llm_key': 'CONTROLLED-KEY'})
    app.save_config({'base_url': 'https://a.example/v1', 'model': 'two', 'llm_key': ''})
    assert app.config()['llm_key'] == 'CONTROLLED-KEY'
    with pytest.raises(app.UserError, match='更换模型地址'):
        app.save_config({'base_url': 'https://b.example/v1', 'model': 'two'})
    assert app.config()['base_url'] == 'https://a.example/v1'


def test_environment_override_is_not_silently_ignored(monkeypatch):
    monkeypatch.setenv('SCOUT_LLM_MODEL', 'environment-model')
    with pytest.raises(app.UserError, match='环境变量'):
        app.save_config({'base_url': 'https://a.example', 'model': 'new-model'})


def test_remote_plaintext_rejected_and_local_model_allowed():
    with pytest.raises(app.UserError, match='HTTPS'):
        app.save_config({'base_url': 'http://public.example/v1', 'model': 'one'})
    assert app.save_config({'base_url': 'http://127.0.0.1:11434/v1', 'model': 'local'})['model_configured']


def test_symlink_not_overwritten(tmp_path):
    target = tmp_path / 'target'
    target.write_text('keep')
    app.CONFIG.symlink_to(target)
    with pytest.raises(app.UserError, match='符号链接'):
        app.atomic_json(app.CONFIG, {'key': 'fixture'})
    assert target.read_text() == 'keep'


def test_failed_search_counts_and_stops_without_retry(monkeypatch):
    s = base_session()
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise app.UserError('元宝搜索返回 HTTP 401')
    monkeypatch.setattr(app, 'post_json', fail)
    with pytest.raises(app.UserError):
        app.run_search(s, {'search_key': 'CONTROLLED'}, '真实查询词', {'Site': 'github.com'})
    assert len(calls) == 1 and s['searches'][0]['status'] == 'failed'
    assert 'CONTROLLED' not in json.dumps(s)


def test_missing_key_and_invalid_filter_do_not_count_calls(monkeypatch):
    s = base_session()
    monkeypatch.setattr(app, 'post_json', lambda *a, **k: pytest.fail('Network should not run'))
    with pytest.raises(app.UserError):
        app.run_search(s, {}, 'query', {})
    with pytest.raises(ValueError):
        app.run_search(s, {'search_key': 'CONTROLLED'}, 'query', {'Site': 'https://github.com'})
    assert s['searches'] == []


def test_advanced_parameters_and_followup_receipt_preserved(monkeypatch):
    s = base_session()
    bodies = []
    def fake(url, payload, *args):
        bodies.append(payload)
        return {'Response': {'RequestId': 'r' + str(len(bodies)), 'Version': 'standard', 'Pages': []}}
    monkeypatch.setattr(app, 'post_json', fake)
    first = app.run_search(s, {'search_key': 'CONTROLLED'}, 'find', {'Site': 'github.com', 'Freshness': 'y1'})
    second = app.run_search(s, {'search_key': 'CONTROLLED'}, 'specific limitation', {'Site': 'docs.example.org'}, first['id'], '根据前次发现追查限制')
    assert second['follows'] == first['id'] and second['request_id'] != first['request_id']
    assert bodies[0]['Freshness'] == 'y1' and second['version'] == 'standard'


def test_fabricated_or_summary_citations_are_removed_and_p1_downgraded():
    s = base_session()
    s['sources'] = [{'id': 'S01', 'level': 'summary', 'text': 'This is only a controlled search summary.'}]
    result = app.clean_round({'questions': [{'question': '选择这个吗？', 'recommendation': '先验证', 'question_evidence': [{'source_id': 'S01', 'quote': s['sources'][0]['text']}]}],
                              'candidates': [{'name': 'fixture', 'priority': 'P1', 'evidence': [{'source_id': 'invented', 'quote': 'A fabricated original source quote.'}]}]}, s)
    assert result['questions'][0]['label'] == '开工侦探提问'
    assert result['questions'][0]['recommendation_evidence'] == []
    assert result['candidates'][0]['priority'] == 'P3'


def test_question_and_recommendation_have_separate_proofs_and_archived_downgrade():
    s = base_session()
    text = 'An original source describes this implementation.'
    s['sources'] = [{'id': 'S01', 'level': 'original', 'text': text, 'repository': {'archived': True}}]
    proof = [{'source_id': 'S01', 'quote': text}]
    r = app.clean_round({'questions': [{'question': '使用它吗？', 'recommendation': '先验证', 'question_evidence': proof}],
                         'candidates': [{'priority': 'P1', 'evidence': proof}]}, s)
    assert r['questions'][0]['label'] == '开工侦探经过元宝搜索后提问'
    assert r['questions'][0]['recommendation_status'] == '访谈建议，尚无原文背书'
    assert r['candidates'][0]['priority'] == 'P3'


def test_independent_review_cannot_invent_candidates_or_upgrade_priority(monkeypatch):
    s = base_session()
    quote = 'A controlled original document with implementation details.'
    s['sources'] = [{'id': 'S01', 'level': 'original', 'text': quote}]
    original = {'name': 'discovered-project', 'priority': 'P2', 'evidence': [{'source_id': 'S01', 'quote': quote}]}
    calls = []
    def fake_model(cfg, instruction, payload):
        calls.append(instruction)
        if instruction == app.AUDIT:
            return {'candidates': [{**original, 'priority': 'P1'}, {**original, 'name': 'invented-project'}], 'questions': []}
        return {'candidates': [original], 'questions': []}
    monkeypatch.setattr(app, 'model', fake_model)
    app.interview(s, {})
    assert calls[-1] == app.AUDIT and len(calls) == 2
    assert [(c['name'], c['priority']) for c in s['rounds'][0]['candidates']] == [('discovered-project', 'P2')]


def test_unsettled_prerequisite_is_not_a_current_frontier_question():
    s = base_session()
    r = app.clean_round({'questions': [
        {'question': '纯前端还是服务端？', 'recommendation': '先选形态', 'blocked_by': []},
        {'question': '后端用什么？', 'recommendation': '先选后端库', 'blocked_by': ['R1Q1']},
        {'question': '验收用哪些样本？', 'recommendation': '固定样本验证', 'blocked_by': []},
    ]}, s)
    assert [q['question'] for q in r['questions']] == ['纯前端还是服务端？', '验收用哪些样本？']


def test_confirmation_requires_actual_answer_and_distinct_dependent_requests():
    s = base_session()
    s['searches'] = [{'id': 'Y01', 'status': 'success', 'request_id': 'one', 'parameters': {'Site': 'github.com'}},
                     {'id': 'Y02', 'status': 'success', 'request_id': 'two', 'follows': 'Y01', 'reason': '追查', 'parameters': {}}]
    s['sources'] = [{'id': 'S01', 'level': 'original'}]
    s['rounds'] = [{'ready': False, 'questions': [{'id': 'R1Q1', 'question_evidence': [{}], 'recommendation_evidence': [{}]}]}, {'ready': True, 'questions': []}]
    assert not app.gate(s)['can_confirm']
    s['messages'] = [{'answers': [{'question_id': 'R1Q1', 'answer': '是'}]}]
    assert app.gate(s)['can_confirm']
    s['searches'][1]['request_id'] = 'one'
    assert not app.gate(s)['can_confirm']


@pytest.mark.parametrize('url', ['http://127.0.0.1:8790', 'http://[::1]/', 'http://169.254.169.254/', 'file:///etc/passwd', 'http://a:b@example.com/'])
def test_original_reader_refuses_private_urls(url):
    with pytest.raises(app.UserError):
        app.public_url(url)


def test_reader_follows_discovery_and_keeps_summary_when_original_unavailable(monkeypatch):
    s = base_session()
    def unavailable(url):
        raise app.UserError('blocked')
    monkeypatch.setattr(app, 'read_public', unavailable)
    app.read_sources(s, {'id': 'Y01', 'results': [{'url': 'https://example.com/doc', 'title': 'Example', 'passage': 'Summary only'}]})
    assert s['sources'][0]['level'] == 'summary'
    assert s['sources'][0]['search_id'] == 'Y01'


def test_session_ids_cannot_escape_directory():
    with pytest.raises(app.UserError):
        app.session_path('../../config')


def test_real_http_boundary_does_not_allow_cross_origin_or_tokenless_access():
    srv = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    try:
        with urllib.request.urlopen(base + '/api/status') as resp:
            status = json.load(resp)
        assert status['token'] == app.TOKEN and not status['config']['search_configured']
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + '/api/sessions')
        req = urllib.request.Request(base + '/api/config', data=b'{}', headers={'Origin': 'https://evil.example', 'X-Kaigong-Token': app.TOKEN})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req)
        req = urllib.request.Request(base + '/api/sessions', headers={'X-Kaigong-Token': app.TOKEN})
        assert json.load(urllib.request.urlopen(req)) == {'sessions': []}
    finally:
        srv.shutdown()
        srv.server_close()


def test_end_to_end_two_round_investigation_and_confirmation(monkeypatch):
    s = base_session()
    quote = 'This project preserves the original video timestamps in notes.'
    app.save_session(s)
    monkeypatch.setattr(app, 'config', lambda: {'search_key': 'SEARCH-FIXTURE', 'base_url': 'https://model.example', 'model': 'fixture'})
    count = {'search': 0, 'model': 0}
    def fake_post(url, payload, *args):
        count['search'] += 1
        return {'Response': {'RequestId': f'controlled-{count["search"]}', 'Pages': [{'url': 'https://example.org/project', 'title': 'Reference', 'passage': quote}]}}
    monkeypatch.setattr(app, 'post_json', fake_post)
    monkeypatch.setattr(app, 'read_public', lambda url: (quote * 3, url))
    def fake_model(cfg, instruction, payload):
        count['model'] += 1
        if instruction == app.AUDIT:
            return payload['draft']
        if instruction == app.PLAN:
            return {'need_search': not s['rounds'], 'query': 'video notes', 'site': 'github.com', 'reason': 'discover'}
        if instruction == app.FOLLOW:
            assert payload['previous_search']['request_id'] == 'controlled-1'
            return {'query': 'project timestamps limits', 'site': 'example.org', 'anchor_url': 'https://example.org/project', 'reason': '核对先前发现的时间点保留能力'}
        if not payload['messages']:
            return {'questions': [{'question': '先保留时间点吗？', 'recommendation': '是，先验证时间点。',
                    'question_evidence': [{'source_id': 'S01', 'quote': quote}], 'recommendation_evidence': [{'source_id': 'S01', 'quote': quote}]}]}
        return {'questions': [], 'ready_for_confirmation': True, 'understanding': '范围已定'}
    monkeypatch.setattr(app, 'model', fake_model)
    app.work(s['id'])
    s = app.load_session(s['id'])
    assert s['status'] == 'waiting' and len(s['searches']) == 2
    assert s['rounds'][0]['questions'][0]['label'] == '开工侦探经过元宝搜索后提问'
    assert not app.gate(s)['can_confirm']
    s['messages'].append({'text': '', 'answers': [{'question_id': 'R1Q1', 'answer': '是'}]})
    app.save_session(s)
    app.work(s['id'])
    final = app.load_session(s['id'])
    assert app.gate(final)['can_confirm']
    report = app.markdown(final)
    assert 'controlled-2' in report and quote in report and 'SEARCH-FIXTURE' not in report
