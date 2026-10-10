import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from careonex_retrieve.parent_context import parent_object, fetch_parent
from careonex_retrieve.retriever import retrieve


RUN = 'chunk-abc-20261009-223721'
BUCKET = 'ac215-program-kb-117949645823'
PARENT_ID = '0123456789abcdef0123'
CHILD_KEY = f'experiments/chunking/{RUN}/hierarchical/chunks/nj_doas/jacc.html/child1.md'
PARENT_KEY = f'experiments/chunking/{RUN}/hierarchical/parents/nj_doas/jacc.html/{PARENT_ID}.md'


class FakeS3:
    def __init__(self):
        self.calls=[]
    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        return {'Body': io.BytesIO(b'JACC parent eligibility and services; detailed original context.')}


class FakeKB:
    def retrieve(self, **kwargs):
        return {'retrievalResults':[{'content':{'text':'JACC eligibility'},
                'location':{'s3Location':{'uri': f's3://{BUCKET}/{CHILD_KEY}'}},
                'metadata':{'parent_id':PARENT_ID, 'title':'JACC'}}]}


def test_parent_object_must_be_in_hierarchical_staging():
    assert parent_object(f's3://{BUCKET}/{CHILD_KEY}',PARENT_ID)==(BUCKET,PARENT_KEY)
    for path in ('chunks/nj_doas/x.md',
        f'experiments/chunking/{RUN}/legacy/chunks/doc/x.md',
        f'experiments/chunking/{RUN}/hierarchical/parents/doc/x.md',
        f'experiments/chunking/{RUN}/hierarchical/chunks/../x.md',
        f'experiments/chunking/bad_run/hierarchical/chunks/doc/x.md'):
        assert parent_object(f's3://{BUCKET}/{path}',PARENT_ID) is None
    assert parent_object(f's3://{BUCKET}/{CHILD_KEY}', '../../anything') is None
    assert parent_object('https://other/path',PARENT_ID) is None


def test_fetch_parent_uses_only_validated_path():
    client=FakeS3()
    text=fetch_parent(client,f's3://{BUCKET}/{CHILD_KEY}',PARENT_ID,allowed_bucket=BUCKET)
    assert text.startswith('JACC parent')
    assert client.calls == [{'Bucket':BUCKET,'Key':PARENT_KEY}]
    assert fetch_parent(client,f's3://wrong/{CHILD_KEY}',PARENT_ID,allowed_bucket=BUCKET) is None
    assert len(client.calls)==1


def test_retrieve_staging_parent_opt_in(monkeypatch):
    monkeypatch.setenv('CAREONEX_RETRIEVE_RERANK','none')
    monkeypatch.setenv('CAREONEX_PARENT_CONTEXT_KB_ID','STAGING')
    monkeypatch.setenv('CAREONEX_PARENT_S3_BUCKET',BUCKET)
    s3=FakeS3()
    r=retrieve(FakeKB(),'STAGING','How do I apply for JACC?',top_k=1,
               latest_only=False,include_parent_context=True,parent_s3_client=s3)
    assert r.passages[0].parent_id==PARENT_ID
    assert 'parent eligibility' in r.passages[0].parent_text
    assert s3.calls[0]['Key']==PARENT_KEY
    with pytest.raises(ValueError,match='staging'):
        retrieve(FakeKB(),'LIVE','How do I apply for JACC?',top_k=1,
               latest_only=False,include_parent_context=True,parent_s3_client=FakeS3())


def test_no_parent_opt_in_performs_no_s3_fetch(monkeypatch):
    monkeypatch.setenv('CAREONEX_RETRIEVE_RERANK','none')
    r=retrieve(FakeKB(),'LIVE','How do I apply for JACC?',top_k=1,latest_only=False)
    assert r.passages[0].parent_text is None


def import_benchmark():
    import importlib.util
    file=Path(__file__).resolve().parents[3]/'evaluation'/'intent_parent_experiment.py'
    spec=importlib.util.spec_from_file_location('careonex_eval_intent_parent',file)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def test_collector_only_two_retrievals_and_parent_comparison(tmp_path,monkeypatch):
    bench=import_benchmark()
    monkeypatch.setenv('CAREONEX_RETRIEVE_RERANK','none')
    from careonex_retrieve import retriever as mod
    import careonex_retrieve.query_expansion as planner
    class FakeModel:
        def converse(self, **kwargs):
            return {'output': {'message': {'content': [{'text': json.dumps({'queries':['New Jersey JACC alternatives'],'broaden_program_filter':True})}]}}}
    s3=FakeS3()
    from concurrent.futures import Future
    class FakeKB:
        def __init__(self): self.calls=0
        def retrieve(self, **kwargs):
            self.calls+=1
            return {'retrievalResults':[{'content':{'text':'JACC eligibility'},
                       'location':{'s3Location':{'uri':f's3://{BUCKET}/{CHILD_KEY}'}},
                       'metadata':{'parent_id':PARENT_ID}}]}
    kb=FakeKB()
    qs=[{'id':'jacc','query':'What can my mother use if not on Medicaid?'}]
    f=tmp_path/'experiment.json'
    output=bench.collect(qs,'STAGING',BUCKET,f,runtime=kb,s3=s3,model=FakeModel(),planner="intent")
    assert kb.calls == 3 # one baseline + original and added intent query
    runs=output['cases'][0]['runs']
    assert set(runs)==set(bench.arms("intent"))
    assert runs['baseline_child']['passages'][0]['sha256']==runs['baseline_parent']['passages'][0]['sha256']
    assert 'JACC parent' in runs['intent_parent']['passages'][0]['parent_text']
    assert not runs['intent_child']['passages'][0].get('parent_text')
    assert len(s3.calls)==1 # cached across arms
    bench.collect(qs,'STAGING',BUCKET,f,runtime=kb,s3=s3,model=FakeModel(),planner="intent")
    assert kb.calls == 3 # safe resume


def test_grade_only_when_all_passages_reviewed():
    bench=import_benchmark()
    sample={'top_k':5,'cases':[{'id':'one','runs':{
         'baseline_child':{'passages':[{'sha256':'a','relevance':2}], 'latency_ms':20},
         'intent_child':{'passages':[{'sha256':'a','relevance':2}], 'latency_ms':30}
    }}]}
    x=bench.score_retrieval(sample)
    assert x['fully_graded_cases']==1
    assert x['averages']['baseline_child']['mrr_at_5']==1
    sample['cases'][0]['runs']['intent_child']['passages'][0]['relevance']=None
    assert bench.score_retrieval(sample)['fully_graded_cases']==0


def test_model_answer_scoring_is_manual():
    bench=import_benchmark()
    cases={'cases':[{'answers':{a:{'factual_correctness':None,'evidence_grounding':None,'question_coverage':None}
               for a in bench.ARMS}}]}
    assert all(n==0 for n in bench.answer_score(cases)['scored_count'].values())
