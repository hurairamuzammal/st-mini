# Software Testing: Mutation Testing Assignment
## Project: LocalCUA Agent - Preprocessing Module

This repository contains the implementation and rigorous mutation testing suite for the `preprocess_utils.py` module. This module serves as a critical component of the **LocalCUA Agent**, handling DOM sanitization, whitespace normalization, and interactive element visibility detection.

---

## 🚀 Getting Started

### 1. Prerequisites
Ensure you are in the project root directory and have your virtual environment activated.
```bash
pip install mutmut pytest pytest-cov
```

### 2. Project Structure
- **Target Module**: `pretraining_scripts/Mind2Web-main/src/data_utils/preprocess_utils.py`
  - *Contains high-complexity business logic for DOM processing.*
- **Test Suite**: `testing_st/test_preprocess_utils.py`
  - *Specifically designed to handle boundary conditions and edge cases.*
- **Configuration**: `setup.cfg`
  - *Pre-configured to target the correct source and test directories.*

---

## 📈 Assignment Flow (Numbered Guide)

To complete the assignment successfully, follow this structured flow:

### 1. Baseline Assessment (Task 1)
Establish the current state of your test suite.
- **Run Coverage**:
  ```bash
  pytest testing_st/test_preprocess_utils.py --cov=pretraining_scripts/Mind2Web-main/src/data_utils/preprocess_utils.py --cov-report=html:reports/baseline_coverage
  ```
- **Analysis**: Open `reports/baseline_coverage/index.html`. 
  - *Observation*: High line coverage (~90%+) does **not** mean all logic is safe. Note down any uncovered branches.

### 2. Initial Mutation Run (Task 2)
Identify "Survived Mutants" that current tests miss.
- **Execution**:
  ```bash
  mutmut run
  ```
- **Check Results**:
  ```bash
  mutmut results
  ```
- **Identify Weaknesses**: Look for `ROR` (Relational Operator Replacement) or `LCR` (Logical Connector Replacement) mutants that survived.

### 3. Deep Analysis (Task 3)
Perform root-cause analysis on survived mutants.
- **Inspect a Mutant**:
  ```bash
  mutmut show <mutant_id>
  ```
- **Assignment Requirement**: Select 3-5 survived mutants.
  - At least one **ROR** (e.g., `>` changed to `>=`).
  - At least one **LCR** (e.g., `and` changed to `or`).
  - Document why the current tests failed to catch these.

### 4. Mutant Eradication (Task 3 & 4)
Write targeted "Killer Tests".
- **Action**: Add new test cases to `testing_st/test_preprocess_utils.py` specifically targeting the boundaries identified in the analysis.
- **Strategy**: If a mutant changed `width > 0` to `width >= 0`, add a test case where `width` is exactly `0`.

### 5. Final Verification (Task 4)
Confirm the logic is now bulletproof.
- **Re-run Mutation**:
  ```bash
  mutmut run
  ```
- **Compare Scores**: Verify that your Mutation Score has increased (ideally by 5% or more).
- **Generate Report**:
  ```bash
  mutmut html
  ```

---

## 🛠 Target Logic Explanation

The `preprocess_utils.py` module is a prime candidate for mutation testing because it contains:
1. **Relational Logic**: `is_visible` checks boundaries like `width > 0`.
2. **Complex Conditionals**: `is_empty` uses multiple `and`/`or` chains to filter DOM nodes.
3. **Data Sanitization**: `clean_format_str` performs multi-step string transformations.

### Mutation Operators Targeted:
- **ROR**: Replacing `>` with `>=` in visibility checks.
- **LCR**: Swapping `and` for `or` in the node filtering logic.
- **AOR**: Modifying arithmetic in coordinate calculations.
- **SVR**: Changing return values from `True` to `False`.

---

## 📝 Deliverables Checklist
- [ ] **PDF Report**: Follow the naming convention `FYP-[GroupID]-MutationTesting-Report.pdf`.
- [ ] **GitHub Branch**: Work in a branch named `mutation-testing-assignment`.
- [ ] **Commit History**: Show at least 3 incremental commits adding tests.
- [ ] **HTML Reports**: Include folders `reports/mutation_baseline/` and `reports/mutation_final/`.

---
*Created for CS-4006: Software Testing (Spring 2025)*
