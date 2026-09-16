# 05-evaluation-architecture.md

## 1. Purpose

Evaluation 负责回答：

> Runtime 执行得怎么样？

Evaluation 与 Runtime 解耦。

---

## 2. Architecture

```text
                 Runtime
                    |
              Execution Data
                    |
                    v
               Evaluation
                    |
       +------------+-------------+
       |            |             |
    Dataset       Evaluator     Judge
       |            |             |
       +------------+-------------+
                    |
                    v
                 Report
```

---

## 3. Evaluation Layers

### Offline Evaluation

用于：

* Dataset
* Golden Cases
* Regression
* Model Comparison
* Prompt Comparison
* Agent Version Comparison

### Online Evaluation

用于：

* Production Run Sampling
* Quality Monitoring
* User Feedback
* Failure Detection

---

## 4. Evaluator

预留：

```text
ExactMatchEvaluator
JSONDiffEvaluator
RecallEvaluator
RubricEvaluator
LLMJudgeEvaluator
CustomEvaluator
```

Evaluator 必须具备统一接口。

---

## 5. Evaluation Data

统一记录：

```text
Input
Expected Output
Actual Output
Metadata
Tool Calls
Events
Trace
Score
Feedback
```

---

## 6. Evaluation -> Improvement

未来支持：

```text
Production Run
      ↓
Evaluation
      ↓
Failure Case
      ↓
Dataset
      ↓
Experiment
      ↓
Prompt / Skill / Model Change
      ↓
Evaluation
```

V1 不实现自动优化闭环。

---

## 7. Non-goals

Evaluation 不负责：

* Agent Runtime
* Prompt Runtime
* Model Invocation
* Tool Invocation
* Workflow Execution
