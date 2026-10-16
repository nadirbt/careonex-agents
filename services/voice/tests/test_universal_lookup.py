"""Voice side of the universal home-care lookup (moved from the retrieve tests: they import nova_sonic)."""
from nova_sonic import tools as voice_tools
from nova_sonic.config import DEFAULT_SYSTEM_PROMPT
from nova_sonic.tools import LOOKUP_PROGRAM_INFO


def test_prompt_and_tool_are_broad_but_retrieval_is_on_demand():
    prompt = DEFAULT_SYSTEM_PROMPT.lower()
    description = LOOKUP_PROGRAM_INFO['toolSpec']['description'].lower()
    assert 'complete specific factual question' in prompt
    assert 'relevant to home care' in prompt
    assert 'not just government programs' in description
    for keyword in ('dementia', 'cost', 'caregiver', 'scheduling'):
        assert keyword in description
    assert 'do not call retrieval for greetings' in prompt
    assert 'never invent careonex prices' in prompt


def test_general_voice_lookup_does_not_force_program_filter(monkeypatch):
    seen=[]
    monkeypatch.setenv('CAREONEX_VOICE_SEARCH_MODE', 'feedback')
    monkeypatch.setattr(voice_tools, 'RETRIEVE_URL', 'http://retrieve:8080')
    monkeypatch.setattr(voice_tools, '_post_json',
        lambda url, payload, timeout: (seen.append(payload),
            {'passages':[{'text':'General home care help','title':'Caregiver support'}]})[1])
    result=voice_tools.lookup_program_info_sync({'query':'Can a caregiver help with cooking?'})
    assert len(result['passages']) == 1
    assert seen[0]['search_mode'] == 'feedback'
    assert 'program' not in seen[0]
