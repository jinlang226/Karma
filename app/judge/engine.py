import csv
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

from app.settings import ROOT
from app.util import ts_str

from .classification import evaluate_classifiers
from .client import OpenAICompatibleJudgeClient
from .evidence import validate_evidence_ids
from .input_builder import JudgeInputBuilder
from .rubric import load_merged_rubric
from .scoring import compute_weighted_scores


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_json_loose(text):
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty judge response")

    try:
        return json.loads(text)
    except Exception:
        pass

    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        try:
            return json.loads("\n".join(lines).strip())
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("unable to parse judge JSON")


def _read_jsonl(path):
    rows = []
    try:
        p = Path(path)
        if not p.exists():
            return rows
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except Exception:
                continue
    except Exception:
        return []
    return rows


def _infer_service_case_from_workflow(run_root):
    stage_rows = _read_jsonl(run_root / "workflow_stage_results.jsonl")
    for row in stage_rows:
        run_dir = str((row or {}).get("run_dir") or "").strip()
        if not run_dir:
            continue
        stage_run_root = Path(run_dir)
        if not stage_run_root.is_absolute():
            stage_run_root = (ROOT / stage_run_root).resolve()
        meta = _read_json(stage_run_root / "meta.json") or {}
        service = str(meta.get("service") or "").strip()
        case = str(meta.get("case") or "").strip()
        if service and case:
            return service, case

    workflow_state = _read_json(run_root / "workflow_state.json") or {}
    for row in (workflow_state.get("stage_statuses") or []):
        run_dir = str((row or {}).get("run_dir") or "").strip()
        if not run_dir:
            continue
        stage_run_root = Path(run_dir)
        if not stage_run_root.is_absolute():
            stage_run_root = (ROOT / stage_run_root).resolve()
        meta = _read_json(stage_run_root / "meta.json") or {}
        service = str(meta.get("service") or "").strip()
        case = str(meta.get("case") or "").strip()
        if service and case:
            return service, case
    return "", ""


def _sanitize_model_output(payload):
    if not isinstance(payload, dict):
        return {
            "dimension_scores": [],
            "milestone_coverage": {"covered": [], "missed": []},
            "anti_pattern_flags": [],
            "overall_assessment": "",
            "limitations": ["judge model output was not a JSON object"],
        }

    out = {
        "dimension_scores": [],
        "milestone_coverage": payload.get("milestone_coverage")
        if isinstance(payload.get("milestone_coverage"), dict)
        else {"covered": [], "missed": []},
        "anti_pattern_flags": payload.get("anti_pattern_flags")
        if isinstance(payload.get("anti_pattern_flags"), list)
        else [],
        "overall_assessment": str(payload.get("overall_assessment") or "").strip(),
        "limitations": payload.get("limitations") if isinstance(payload.get("limitations"), list) else [],
    }

    for item in payload.get("dimension_scores") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        if not qid:
            continue
        out["dimension_scores"].append(
            {
                "id": qid,
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "evidence_ids": item.get("evidence_ids")
                if isinstance(item.get("evidence_ids"), list)
                else [],
                "rationale": str(item.get("rationale") or item.get("reason") or "").strip(),
            }
        )
    return out


def _schema_hint():
    return {
        "dimension_scores": [
            {
                "id": "string",
                "score": "number 0..5 or null if insufficient evidence",
                "confidence": "number 0..1",
                "evidence_ids": [
                    "agent.log:L000010-L000020",
                    "external_metrics:read_write_ratio.total_commands",
                    "agent_usage:totals.total_tokens",
                    "efficiency_facts:time_to_success_seconds",
                    "workflow:stage_results.stage_seed.status",
                    "workflow:final_sweep.regression.stage_seed.classification",
                    "workflow_efficiency_facts:total_stage_attempts",
                ],
                "rationale": "short evidence-backed reason",
            }
        ],
        "milestone_coverage": {
            "covered": ["milestone text"],
            "missed": ["milestone text"],
        },
        "anti_pattern_flags": [
            {
                "id": "label",
                "severity": "low|medium|high",
                "evidence_ids": ["agent.log:L000010-L000020"],
                "rationale": "why this anti-pattern applies",
            }
        ],
        "overall_assessment": "1-3 sentences",
        "limitations": ["optional list of evidence gaps"],
        "classifications": [
            {
                "classifier_id": "string",
                "label": "string",
                "rule_id": "string|null",
                "confidence": "number 0..1",
                "evidence_ids": ["agent.log:L000010-L000020"],
                "rationale": "short evidence-backed reason",
            }
        ],
    }


def _build_messages(rubric, judge_input, prompt_version):
    judge_input_for_prompt = _sanitize_judge_input_for_prompt(judge_input)
    workflow_context = judge_input_for_prompt.get("workflow_context") if isinstance(judge_input_for_prompt, dict) else {}
    workflow_enabled = bool((workflow_context or {}).get("workflow_enabled"))
    system = (
        "You are an evaluator for AI-agent benchmark trajectories. "
        "Judge process quality from evidence, not outcome alone. "
        "Do not hallucinate unseen actions. "
        "Use only provided data. "
        "For each score, cite evidence references in evidence_ids "
        "(agent.log line ranges like agent.log:L000120-L000140 for trajectory evidence; "
        "external_metrics/agent_usage/efficiency_facts/workflow_efficiency_facts paths for efficiency evidence; "
        "workflow:<path> for workflow context evidence). "
        "Return strict JSON only."
    )

    user = {
        "task": "Score this run trajectory against the rubric dimensions.",
        "mode": "single_pass",
        "prompt_version": prompt_version,
        "bias_controls": [
            "Avoid outcome bias (pass/fail should not dominate process scoring).",
            "Penalize blind guessing and repeated retries without new evidence.",
            "Reward evidence-driven diagnosis, validation, and robust fix behavior.",
            "Use null score when evidence is insufficient.",
            "For workflow runs: do not penalize expected regressions in final sweep; penalize unexpected regressions.",
        ],
        "evidence_contract": {
            "allowed_formats": [
                "agent.log:L000120-L000140",
                "external_metrics:read_write_ratio.total_commands",
                "external_metrics:time_to_first_mutation.time_to_success_seconds",
                "agent_usage:totals.total_tokens",
                "efficiency_facts:time_to_success_seconds",
                "workflow_efficiency_facts:total_stage_attempts",
                "workflow:stage_results.stage_seed.status",
                "workflow:final_sweep.regression.stage_seed.classification",
            ],
            "notes": [
                "Use line IDs visible in judge_input.blocks.agent_log.text_numbered.",
                "For resource_efficiency, cite external_metrics/agent_usage/efficiency_facts evidence instead of only agent.log lines.",
                "For workflow-aware judgments, cite workflow:* references for stage transitions and regressions.",
                "Do not cite approximate or unnumbered ranges.",
            ],
        },
        "rubric": {
            "rubric_id": rubric.get("rubric_id"),
            "rubric_version": rubric.get("rubric_version"),
            "objective_weights": rubric.get("objective_weights"),
            "questions": rubric.get("questions"),
            "milestones": rubric.get("milestones"),
            "anti_patterns": rubric.get("anti_patterns"),
            "prompt_notes": rubric.get("prompt_notes"),
        },
        "judge_input": judge_input_for_prompt,
        "required_output_schema": _schema_hint(),
    }
    if workflow_enabled:
        user["workflow_instructions"] = [
            "Evaluate stage-by-stage trajectory quality and transition handling.",
            "Use workflow.final_sweep.expected/observed/regression evidence to distinguish expected vs unexpected regressions.",
            "If workflow evidence is incomplete, return null for affected dimensions instead of guessing.",
        ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, indent=2)},
    ]


def _sanitize_judge_input_for_prompt(judge_input):
    if not isinstance(judge_input, dict):
        return judge_input
    out = deepcopy(judge_input)
    blocks = out.get("blocks")
    if isinstance(blocks, dict):
        agent_log = blocks.get("agent_log")
        if isinstance(agent_log, dict):
            agent_log.pop("text", None)
    return out


def _summarize_markdown(result):
    lines = [
        "# Trajectory Judge Summary",
        "",
        f"- Judge status: {result.get('judge_status')}",
        f"- Model: {result.get('judge_model')}",
        f"- Prompt version: {result.get('prompt_version')}",
        f"- Service/Case: {result.get('service')}/{result.get('case')}",
        f"- Run dir: {result.get('run_dir')}",
    ]

    scores = result.get("scores") or {}
    if scores:
        lines.extend(
            [
                f"- Final score (0-5): {scores.get('final_score')}",
                f"- Process quality: {scores.get('process_quality_score')}",
                f"- Efficiency: {scores.get('efficiency_score')}",
                f"- Avg confidence: {scores.get('average_confidence')}",
            ]
        )
    classifications = result.get("classifications") or {}
    if isinstance(classifications, dict) and classifications:
        lines.append("")
        lines.append("## Classifications")
        for cid, row in sorted(classifications.items()):
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "unknown")
            conf = row.get("confidence")
            if conf is None:
                lines.append(f"- {cid}: {label}")
            else:
                lines.append(f"- {cid}: {label} (confidence={conf})")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("## Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")

    if result.get("error"):
        lines.append("")
        lines.append("## Error")
        lines.append(f"- {result.get('error')}")

    return "\n".join(lines) + "\n"


class TrajectoryJudge:
    def __init__(
        self,
        base_url,
        api_key,
        model,
        timeout_sec=120,
        max_retries=2,
        prompt_version="v1",
        fail_open=True,
        include_outcome=False,
        dry_run=False,
    ):
        self.base_url = (base_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.timeout_sec = int(timeout_sec)
        self.max_retries = int(max_retries)
        self.prompt_version = str(prompt_version or "v1")
        self.fail_open = bool(fail_open)
        self.include_outcome = bool(include_outcome)
        self.dry_run = bool(dry_run)

        referer = os.environ.get("JUDGE_HTTP_REFERER") or "https://github.com/jimmyouyang/kubernetes-microservice-benchmark"
        title = os.environ.get("JUDGE_HTTP_TITLE") or "kubernetes-microservice-benchmark"

        self.client = OpenAICompatibleJudgeClient(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model,
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            referer=referer,
            title=title,
        )

    @classmethod
    def from_args(cls, args):
        llm_env = getattr(args, "_llm_env", None)
        llm_env = llm_env if isinstance(llm_env, dict) else {}
        base_url = (
            getattr(args, "judge_base_url", None)
            or llm_env.get("JUDGE_BASE_URL")
            or os.environ.get("JUDGE_BASE_URL")
            or llm_env.get("LLM_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or "https://openrouter.ai/api/v1"
        )
        api_key = (
            getattr(args, "judge_api_key", None)
            or llm_env.get("JUDGE_API_KEY")
            or os.environ.get("JUDGE_API_KEY")
            or llm_env.get("LLM_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or llm_env.get("OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        model = (
            getattr(args, "judge_model", None)
            or llm_env.get("JUDGE_MODEL")
            or os.environ.get("JUDGE_MODEL")
            or llm_env.get("LLM_MODEL")
            or os.environ.get("LLM_MODEL")
            or "openai/gpt-4o-mini"
        )
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_sec=getattr(args, "judge_timeout", 120),
            max_retries=getattr(args, "judge_max_retries", 2),
            prompt_version=getattr(args, "judge_prompt_version", "v1"),
            fail_open=getattr(args, "judge_fail_open", True),
            include_outcome=getattr(args, "judge_include_outcome", False),
            dry_run=getattr(args, "dry_run", False),
        )

    def evaluate_run(self, run_dir, service=None, case=None):
        run_root = Path(run_dir)
        if not run_root.is_absolute():
            run_root = (ROOT / run_root).resolve()
        judge_dir = run_root / "judge"
        if judge_dir.exists():
            shutil.rmtree(judge_dir)
        judge_dir.mkdir(parents=True, exist_ok=True)

        warnings = []
        error = None
        raw_response = None
        model_output = {}
        scores = {}
        evidence_validation = {}
        classifications = {}

        meta_payload = _read_json(run_root / "meta.json") or {}
        service = service or str(meta_payload.get("service") or "")
        case = case or str(meta_payload.get("case") or "")
        if not service or not case:
            wf_service, wf_case = _infer_service_case_from_workflow(run_root)
            if not service:
                service = wf_service
            if not case:
                case = wf_case

        if not service or not case:
            warnings.append("service/case missing in run metadata; rubric fallback may be generic")

        rubric = load_merged_rubric(service, case, warnings)
        builder = JudgeInputBuilder(include_outcome=self.include_outcome)
        judge_input, context = builder.build(run_root, rubric)
        warnings.extend(context.get("warnings") or [])

        input_path = judge_dir / "input_v1.json"
        input_path.write_text(json.dumps(judge_input, indent=2), encoding="utf-8")

        prompt_messages = _build_messages(rubric, judge_input, self.prompt_version)
        prompt_path = judge_dir / f"prompt_{self.prompt_version}.json"
        prompt_path.write_text(json.dumps(prompt_messages, indent=2), encoding="utf-8")

        if self.dry_run:
            warnings.append("judge dry-run enabled: LLM call skipped")
        else:
            try:
                llm = self.client.create_judgement(prompt_messages)
                raw_response = llm.get("raw_response")
                raw_path = judge_dir / "raw_response.json"
                raw_path.write_text(json.dumps(raw_response, indent=2), encoding="utf-8")

                parsed = _parse_json_loose(llm.get("content"))
                model_output = _sanitize_model_output(parsed)
                evidence_validation = validate_evidence_ids(
                    model_output.get("dimension_scores") or [],
                    judge_input,
                )
                if evidence_validation.get("invalid_count"):
                    warnings.append(
                        f"judge returned {evidence_validation.get('invalid_count')} invalid evidence reference(s)"
                    )
                if evidence_validation.get("unvalidated_count"):
                    warnings.append(
                        f"judge returned {evidence_validation.get('unvalidated_count')} unvalidated evidence reference(s)"
                    )
                scores = compute_weighted_scores(rubric, model_output)
                classifications = evaluate_classifiers(rubric, judge_input, scores)
            except Exception as exc:
                error = str(exc)
                warnings.append(f"judge evaluation failed: {exc}")
                if not self.fail_open:
                    raise

        run_rel = str(run_root.relative_to(ROOT)) if run_root.is_relative_to(ROOT) else str(run_root)
        result = {
            "schema_version": "judge_result.v1",
            "judge_status": "dry_run" if self.dry_run else ("ok" if not error else "error"),
            "judge_model": self.model,
            "prompt_version": self.prompt_version,
            "dry_run": self.dry_run,
            "evaluated_at": ts_str(),
            "run_dir": run_rel,
            "service": service,
            "case": case,
            "rubric": {
                "rubric_id": rubric.get("rubric_id"),
                "rubric_version": rubric.get("rubric_version"),
                "source": rubric.get("source"),
            },
            "scores": scores,
            "classifications": classifications,
            "model_output": model_output,
            "evidence_validation": evidence_validation,
            "warnings": warnings,
            "error": error,
        }

        result_path = judge_dir / "result_v1.json"
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        summary_path = judge_dir / "summary.md"
        summary_path.write_text(_summarize_markdown(result), encoding="utf-8")

        if not self.dry_run:
            self._update_meta(run_root, result)

        rel_result = str(result_path.relative_to(ROOT)) if result_path.is_relative_to(ROOT) else str(result_path)
        rel_summary = str(summary_path.relative_to(ROOT)) if summary_path.is_relative_to(ROOT) else str(summary_path)
        rel_input = str(input_path.relative_to(ROOT)) if input_path.is_relative_to(ROOT) else str(input_path)
        rel_prompt = str(prompt_path.relative_to(ROOT)) if prompt_path.is_relative_to(ROOT) else str(prompt_path)
        return {
            "judge_status": result.get("judge_status"),
            "final_score": (scores or {}).get("final_score"),
            "process_quality_score": (scores or {}).get("process_quality_score"),
            "efficiency_score": (scores or {}).get("efficiency_score"),
            "average_confidence": (scores or {}).get("average_confidence"),
            "classifications": classifications,
            "result_path": rel_result,
            "summary_path": rel_summary,
            "input_path": rel_input,
            "prompt_path": rel_prompt,
            "dry_run": self.dry_run,
            "warnings": warnings,
            "error": error,
            "service": service,
            "case": case,
            "run_dir": run_rel,
        }

    def write_batch_summary(self, batch_dir, run_results):
        batch_root = Path(batch_dir)
        batch_root.mkdir(parents=True, exist_ok=True)

        index_rows = []
        for item in run_results or []:
            index_rows.append(
                {
                    "run_dir": item.get("run_dir"),
                    "service": item.get("service"),
                    "case": item.get("case"),
                    "judge_status": item.get("judge_status"),
                    "final_score": item.get("final_score"),
                    "process_quality_score": item.get("process_quality_score"),
                    "efficiency_score": item.get("efficiency_score"),
                    "average_confidence": item.get("average_confidence"),
                    "result_path": item.get("result_path"),
                    "error": item.get("error"),
                }
            )

        index_path = batch_root / "judge_index.json"
        index_path.write_text(json.dumps(index_rows, indent=2), encoding="utf-8")

        ok_rows = [item for item in index_rows if item.get("judge_status") == "ok" and item.get("final_score") is not None]
        avg_final = None
        if ok_rows:
            avg_final = round(sum(float(item["final_score"]) for item in ok_rows) / len(ok_rows), 4)

        by_case = {}
        for item in index_rows:
            case_key = f"{item.get('service')}/{item.get('case')}"
            by_case.setdefault(case_key, []).append(item)

        summary = {
            "schema_version": "judge_batch_summary.v1",
            "generated_at": ts_str(),
            "total_runs": len(index_rows),
            "ok_runs": sum(1 for item in index_rows if item.get("judge_status") == "ok"),
            "dry_run_runs": sum(1 for item in index_rows if item.get("judge_status") == "dry_run"),
            "error_runs": sum(1 for item in index_rows if item.get("judge_status") == "error"),
            "average_final_score": avg_final,
            "by_case": {
                key: {
                    "count": len(rows),
                    "ok": sum(1 for row in rows if row.get("judge_status") == "ok"),
                    "average_final_score": (
                        round(
                            sum(float(row["final_score"]) for row in rows if row.get("final_score") is not None)
                            / max(1, sum(1 for row in rows if row.get("final_score") is not None)),
                            4,
                        )
                        if any(row.get("final_score") is not None for row in rows)
                        else None
                    ),
                }
                for key, rows in sorted(by_case.items())
            },
        }

        summary_path = batch_root / "judge_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        csv_path = batch_root / "judge_leaderboard.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "run_dir",
                    "service",
                    "case",
                    "judge_status",
                    "final_score",
                    "process_quality_score",
                    "efficiency_score",
                    "average_confidence",
                ]
            )
            for row in index_rows:
                writer.writerow(
                    [
                        row.get("run_dir"),
                        row.get("service"),
                        row.get("case"),
                        row.get("judge_status"),
                        row.get("final_score"),
                        row.get("process_quality_score"),
                        row.get("efficiency_score"),
                        row.get("average_confidence"),
                    ]
                )

        return {
            "judge_index_path": str(index_path),
            "judge_summary_path": str(summary_path),
            "judge_leaderboard_path": str(csv_path),
        }

    def _update_meta(self, run_root, result):
        path = run_root / "meta.json"
        payload = _read_json(path)
        if not isinstance(payload, dict):
            return
        result_path = run_root / "judge" / "result_v1.json"
        payload["judge_status"] = result.get("judge_status")
        payload["judge_path"] = (
            str(result_path.relative_to(ROOT)) if result_path.is_relative_to(ROOT) else str(result_path)
        )
        payload["judge_final_score"] = (result.get("scores") or {}).get("final_score")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
