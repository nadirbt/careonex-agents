"""CareOneX model-free feedback + parent-context benchmark.

This is a CLI entry point for the 2x2 experiment implemented in
intent_parent_experiment.py.  The default planner is ``feedback`` and does not
invoke Amazon Nova Lite or require bedrock:InvokeModel permissions.
"""
from intent_parent_experiment import main

if __name__ == '__main__':
    main()
