# Backend Testing Plan

This backend is the Python side of the project. It is tested separately from the Flutter app so the two testing stacks do not get mixed up.

## Planned Testing Type

The backend uses:

- Unit testing with `pytest`
- Coverage measurement with `pytest-cov`
- Mutation testing with `mutmut` on Linux or WSL2

## Why this is separate from Flutter testing

Flutter already uses its own `test/` folder and Dart test runner. To avoid confusion, the Python backend uses a separate backend-specific test folder and pytest configuration.

## Repository layout

- Backend code stays in `localcua/backend`
- Python tests live at the repository root in `testing_st/`
- Coverage and mutation reports live at the repository root in `reports/`

## Current mutation-testing target

The current target module is [agent/action_parser.py](agent/action_parser.py).

The tests focus on these behaviors:

- numeric rounding helpers
- bounding-box normalization
- parsing action strings
- resolving coordinates for parsed actions

## Local workflow

From the repository root, or from `backend` with the config above:

```bash
python -m pip install pytest pytest-cov mutmut
python -m pytest testing_st -q
python -m pytest testing_st -q --cov=agent.action_parser --cov-branch --cov-report=term-missing --cov-report=html:reports/baseline_coverage
```

## Mutation testing note

`mutmut` does not run natively on Windows. Use WSL2 or Linux to run the mutation pass.
