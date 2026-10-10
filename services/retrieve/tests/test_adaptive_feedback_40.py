"""40 saved real baseline passages: assertions concern planning only, not accuracy.

The previous AWS result is a frozen source for off-line regressions.
The model-free planner cannot prove improved retrieval until rerun on AWS.
"""
from pathlib import Path
import json
from careonex_retrieve.feedback_expansion import plan_feedback_queries
from careonex_retrieve.parent_selection import select_best_parent_contexts
from careonex_retrieve.retriever import Passage

SOURCE = Path(__file__).resolve().parents[3] / 'evaluation' / 'universal_homecare_v2_full_prior.json'


def test_40_pilot_replay_preserves_intent_and_avoids_known_drifts():
    data = json.loads(SOURCE.read_text(encoding='utf-8'))
    assert len(data['cases']) == 40
    plans = {}
    for case in data['cases']:
        ps = [Passage(text=p['text'], heading_path=p.get('heading_path'),
                      title=p.get('title'), program=p.get('program'),
                      source_url=p.get('source_url'), effective_date=None,
                      score=None, s3_key=p.get('s3_key'))
              for p in case['runs']['baseline_child']['passages']]
        plan = plan_feedback_queries(case['query'], ps)
        plans[case['id']] = plan
        assert plan.queries[0] == case['query']
        assert len(plan.queries) <= 3
        assert all(len(x) <= 950 and x.strip() for x in plan.queries[1:])
    for case_id in ('schedule_weekends', 'payment_private',
                    'matching_language', 'unsupported_same_day',
                    'complex_nonmedicaid'):
        assert len(plans[case_id].queries) == 1, case_id
    for case_id in ('daily_bathing', 'daily_dressing', 'daily_mobility'):
        assert plans[case_id].reason == 'already_supported', case_id
    assert len(plans['caregiver_burnout'].queries) == 2
    for case_id in ('daily_meals', 'daily_medication', 'dementia_wandering'):
        assert plans[case_id].reason == 'question_rewrite_used', case_id
    # The new fallback may improve a weak question but may never promote
    # an unrelated program from the FIRST retrieved passage into its query.
    assert 'statewide respite care program' not in plans['daily_meals'].queries[1].lower()
    assert 'veteran' not in plans['daily_medication'].queries[1].lower()


def test_parent_selector_picks_missing_meal_preparation_from_lower_ranked_parent():
    data = json.loads(SOURCE.read_text(encoding='utf-8'))
    meal = next(c for c in data['cases'] if c['id'] == 'daily_meals')
    ps = meal['runs']['baseline_parent']['passages']
    selected = select_best_parent_contexts(
        meal['query'], [p['text'] for p in ps], [p.get('parent_text') for p in ps])
    assert [i for i, x in enumerate(selected) if x] == [4]
    assert 'planning, preparing and serving meals' in selected[4]
    assert sum(bool(x) for x in selected) == 1
    bathing = next(c for c in data['cases'] if c['id'] == 'daily_bathing')
    ps = bathing['runs']['baseline_parent']['passages']
    selected = select_best_parent_contexts(
        bathing['query'], [p['text'] for p in ps], [p.get('parent_text') for p in ps])
    assert all(x is None for x in selected)


def test_organization_claim_does_not_expand_from_unrelated_public_policy():
    from careonex_retrieve.feedback_expansion import plan_feedback_queries
    public = Passage(text='A service may offer weekend visits.',
                     heading_path='Public benefits guide', title='Public benefits guide',
                     program='Government program', source_url=None,
                     effective_date=None, score=.7, s3_key='x')
    q = 'Can CareOneX guarantee a caregiver tomorrow morning?'
    p = plan_feedback_queries(q,[public])
    assert p.queries == [q]
    assert p.reason == 'unverified_provider_claim'
