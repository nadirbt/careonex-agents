"""Replay saved first-search passages through the UPDATED planner, without AWS.

This only shows what extra queries the current code would propose. It cannot
show what AWS would return or prove that retrieval accuracy improved.
"""
import argparse
import json
from pathlib import Path

from careonex_retrieve.feedback_expansion import plan_feedback_queries
from careonex_retrieve.retriever import Passage


def preview(data: dict) -> dict:
    out = []
    for case in data['cases']:
        baseline = case['runs']['baseline_child']['passages']
        passages = [Passage(
            text=x.get('text', ''), title=x.get('title'),
            heading_path=x.get('heading_path'), program=x.get('program'),
            s3_key=x.get('s3_key'), score=None,
            source_url=x.get('source_url'), effective_date=None
        ) for x in baseline]
        plan = plan_feedback_queries(case['query'], passages)
        out.append({
            'id': case['id'], 'question': case['query'],
            'previous_expansion_status': case['runs']['feedback_child'].get('expansion_status'),
            'new_planner_reason': plan.reason,
            'new_proposed_queries': plan.queries,
        })
    return {'note': 'OFFLINE REPLAY ONLY; no new search results or relevance scores',
            'cases': out}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path(__file__).with_name('universal_homecare_pilot_previous.json'))
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args()
    result = preview(json.loads(args.input.read_text(encoding='utf-8')))
    for case in result['cases']:
        print(f"{case['id']}: {case['previous_expansion_status']} -> "
              f"{case['new_planner_reason']} ({len(case['new_proposed_queries'])} searches)")
        for query in case['new_proposed_queries'][1:]:
            print('  + ' + query)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print('Saved', args.output)


if __name__ == '__main__':
    main()
