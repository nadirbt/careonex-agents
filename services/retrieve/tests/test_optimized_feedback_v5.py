"""Optimized v5: strong intent safeguards, distinct queries, novel passage admission."""
from careonex_retrieve.feedback_expansion import plan_feedback_queries, corroborates_original_question
from careonex_retrieve.retriever import Passage, retrieve


def hit(key, text, title='Overview', program=None):
    return Passage(s3_key=key, text=text, heading_path=title, title=title,
                   program=program, source_url=None, effective_date=None, score=.8)


def test_compact_medication_query_is_distinct_and_has_comparison():
    question = 'What is the difference between medication reminders and administering medication?'
    plan = plan_feedback_queries(question, [hit('wrong', 'Financial eligibility and income limits')])
    assert plan.reason == 'question_rewrite_used'
    assert len(plan.queries) == 2
    assert question not in plan.queries[1]
    assert 'comparison' in plan.queries[1]
    assert 'administration' in plan.queries[1]


def test_direct_bathing_hit_skips_extra_api_call():
    q = 'My mother needs help showering. What kind of assistance might be available?'
    r = plan_feedback_queries(q, [hit('pca', 'PCA services include bathing in the tub or shower. Assistance with showering is included.')])
    assert r.reason == 'already_supported'
    assert len(r.queries) == 1


def test_public_program_cannot_certify_provider_claim():
    q = 'Do you guarantee a Mandarin-speaking caregiver tomorrow?'
    r = plan_feedback_queries(q, [hit('gov', 'Medicaid eligibility criteria for home health aides')])
    assert r.queries == [q]


def test_only_explicitly_relevant_novel_evidence_can_enter_fusion():
    q = 'Can someone help my mother prepare meals at home?'
    relevant = hit('pca', 'Personal care aides may assist with planning, preparing and serving meals.',
                   'Personal care assistance')
    banking = hit('bank', 'Bank account deposits and investment eligibility assessments', 'Deposits')
    va = hit('va', 'Home health aides may assist with preparing and serving meals.',
             'VA Homemaker and Home Health Aide', program='VA Homemaker and Home Health Aide Care')
    assert corroborates_original_question(q, relevant)
    assert not corroborates_original_question(q, banking)
    assert not corroborates_original_question(q, va)


def test_feedback_keeps_original_if_extra_result_is_irrelevant(monkeypatch):
    monkeypatch.setenv('CAREONEX_RETRIEVE_RERANK', 'none')
    class KB:
        def __init__(self): self.queries=[]
        def retrieve(self, **kwargs):
            query=kwargs['retrievalQuery']['text']
            self.queries.append(query)
            rows=([hit('baseline', 'Medicare general home-care program overview')]
                  if len(self.queries)==1 else
                  [hit('bank', 'Bank deposits and investment account policies')])
            return {'retrievalResults': [dict(
                content={'text': p.text}, metadata={'title':p.title},
                location={'s3Location': {'uri':'s3://bucket/'+p.s3_key}}) for p in rows]}
    kb=KB()
    result=retrieve(kb, 'STAGING', 'What is the difference between medication reminders and administering medication?',
                    latest_only=False, search_mode='feedback')
    assert len(kb.queries) == 2
    assert len(result.passages) == 1
    assert result.passages[0].s3_key == 'baseline'
    assert result.queries_used[1] != result.queries_used[0]


def test_not_rewrite_unsupported_fee_question():
    q = 'Are there cancellation fees, deposits, or other charges?'
    p = plan_feedback_queries(q, [hit('bad', 'Bank deposits and accounts')])
    assert p.queries == [q]
