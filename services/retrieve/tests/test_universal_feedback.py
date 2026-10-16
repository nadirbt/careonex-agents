"""Cross-topic retrieval behavior: all local fixtures, no AWS model permissions."""
from careonex_retrieve.feedback_expansion import plan_feedback_queries
from careonex_retrieve.retriever import Passage


def hit(key, heading, body):
    return Passage(text=body, heading_path=heading, title=heading, s3_key=key,
                   score=.75, source_url=None, program=None, effective_date=None)


def test_non_program_homecare_questions_expand_from_evidence():
    cases = [
        ("Does anyone help with bathing and getting dressed?", "Assistance with bathing and dressing",
         "Staff assist with bathing, dressing, grooming and other daily activities."),
        ("Do you offer overnight supervision for people with dementia?", "Overnight supervision and dementia care",
         "Some care plans include overnight supervision for people with dementia."),
        ("How much does overnight care cost?", "Overnight care cost and pricing",
         "Overnight home care cost and pricing depend on services."),
        ("Can a caregiver speak Spanish to my dad?", "Spanish-speaking caregiver matching",
         "Caregiver matching can account for Spanish language preferences."),
        ("What if my father wanders due to dementia?", "Wandering safety and dementia support",
         "Wandering with dementia may require safety supervision and support."),
        ("Could someone help with transportation to appointments?", "Transportation to medical appointments",
         "Transportation assistance may be available for appointments."),
        ("Does someone visit on weekends?", "Weekend caregiver visits",
         "Weekend visits may be arranged based on needs."),
        ("Can we get short-term relief for the main caregiver?", "Short-term caregiver respite",
         "Short-term caregiver relief is sometimes described as respite."),
    ]
    expected = ['already_supported', 'unverified_provider_claim',
                'feedback_used', 'feedback_used', 'feedback_used',
                'already_supported', 'feedback_used', 'feedback_used']
    for (question, heading, body), reason in zip(cases, expected):
        plan = plan_feedback_queries(question, [hit('s3', heading, body)])
        assert plan.reason == reason, (question, plan)
        assert plan.queries[0] == question
        if reason == 'feedback_used':
            assert heading in plan.queries[1]
            assert plan.queries[1] != question
        else:
            assert plan.queries == [question]


def test_broad_or_unsupported_question_does_not_force_random_expansion():
    q = 'What services do you offer?'
    result = plan_feedback_queries(q, [hit('s3', 'Respite caregiver assistance',
                                           'Services and help for home care needs.')])
    assert result.queries == [q]
    assert result.reason == 'no_specific_question_terms'
    q2 = 'Can I get a refund for a cancelled flight to Chicago?'
    result = plan_feedback_queries(q2, [hit('s3', 'Weekend caregiver scheduling',
                                             'Weekend staff availability and home care services.')])
    assert result.queries == [q2]


def test_named_company_or_language_does_not_invent_answers():
    q = 'Do you have Mandarin-speaking caregivers in Bergen County?'
    result = plan_feedback_queries(q, [hit('s3', 'Spanish-speaking caregiver matching',
                                             'Spanish language matching may vary by county.')])
    assert result.queries == [q]  # no substantive terms overlap


def test_negation_carries_through_all_queries():
    q = 'I do not want a live-in aide. Can someone visit at night instead?'
    p = plan_feedback_queries(q, [hit('s3','Night-time aide visits',
                                      'A night-time aide can visit at night.')])
    assert len(p.queries) == 2
    assert all('do not want a live-in aide' in x for x in p.queries)


def test_explicit_county_guard_is_not_a_program_rule():
    question = 'Are weekday caregivers available in Bergen County?'
    wrong = hit('wrong','Weekday caregivers in Hudson County',
                'Weekday caregivers visit homes in Hudson County.')
    correct = hit('right','Weekday caregivers in Bergen County',
                  'Weekday caregivers visit homes in Bergen County.')
    wrong_plan = plan_feedback_queries(question, [wrong])
    assert wrong_plan.queries[0] == question
    assert all('Hudson' not in query for query in wrong_plan.queries[1:])
    plan = plan_feedback_queries(question, [wrong, correct])
    assert len(plan.queries) == 2
    assert 'Bergen County' in plan.queries[1]
    assert 'Hudson County' not in plan.queries[1]
