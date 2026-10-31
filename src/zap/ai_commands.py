"""`zap ai ...`: the M8 commands. Paid steps only spend when LLM_MODE=live is set for that run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from zap.config import REPO_ROOT, Settings, get_settings

ai_app = typer.Typer(help="Enrichment, RAG and text-to-SQL (milestone M8).", no_args_is_help=True)

RESULTS = REPO_ROOT / "eval" / "results"
DatasetOpt = Annotated[str, typer.Option(help="Warehouse to use.")]
FASTEMBED_CACHE = Path.home() / ".cache" / "fastembed"


def _save(name: str, report: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def _warehouse(settings: Settings, dataset: str) -> Path:
    db_path = settings.duckdb_path(dataset)
    if not db_path.exists():
        typer.echo(f"ERROR: no warehouse for dataset '{dataset}' ({db_path})", err=True)
        raise typer.Exit(code=1)
    return db_path


def _retriever() -> Any:
    from zap.rag.corpus import build_corpus
    from zap.rag.retrieve import LocalEmbedder, LocalReranker, Retriever

    return Retriever(
        build_corpus(REPO_ROOT), LocalEmbedder(FASTEMBED_CACHE), LocalReranker(FASTEMBED_CACHE)
    )


@ai_app.command("usage")
def usage_cmd() -> None:
    """Show what has been spent, from the ledger in eval/replay/."""
    from zap.llm.client import LlmClient, usage_summary

    settings = get_settings()
    for purpose, row in usage_summary(settings.llm_replay_dir).items():
        typer.echo(f"  {purpose:16} {row['calls']:>4} calls  ${row['cost_usd']:.4f}")
    spent = LlmClient(settings).spent_usd()
    typer.echo(f"Spent ${spent:.4f} of ${settings.llm_budget_usd:.2f}")


@ai_app.command("rag-eval")
def rag_eval_cmd() -> None:
    """Measure retrieval on the golden set. Runs locally and costs nothing."""
    from zap.rag.evaluate import check_golden, evaluate_retrieval, load_golden

    retriever = _retriever()
    questions = load_golden(REPO_ROOT / "eval" / "rag_golden.yml")
    broken = check_golden(questions, list(retriever.chunks.values()))
    if broken:
        typer.echo(f"ERROR: no chunk holds the evidence for {broken}", err=True)
        raise typer.Exit(code=1)
    report = evaluate_retrieval(retriever, questions)
    for mode, scores in report["modes"].items():
        typer.echo(f"  {mode:14} recall@5 {scores['recall_at_5']:.3f}  MRR {scores['mrr']:.3f}")
    typer.echo(f"Report: {_save('rag_retrieval', report)}")


@ai_app.command("rag-answers")
def rag_answers_cmd() -> None:
    """Answer the golden questions and judge the answers. Spends money when live."""
    import yaml

    from zap.llm.client import LlmClient
    from zap.rag.answer import evaluate_answers
    from zap.rag.evaluate import load_golden

    questions = load_golden(REPO_ROOT / "eval" / "rag_golden.yml")
    extra = yaml.safe_load(
        (REPO_ROOT / "eval" / "rag_unanswerable.yml").read_text(encoding="utf-8")
    )
    report = evaluate_answers(LlmClient(get_settings()), _retriever(), questions, extra)
    typer.echo(
        f"Answered {report['answered']} of {report['questions']}; "
        f"cited the evidence {report['cited_the_evidence_rate']:.1%}; "
        f"faithful {report['faithful_rate']:.1%}; declined "
        f"{report['declined_unanswerable']} of {report['unanswerable_questions']} unanswerable"
    )
    typer.echo(f"Report: {_save('rag_answers', report)}")


@ai_app.command("sql-eval")
def sql_eval_cmd(dataset: DatasetOpt = "full") -> None:
    """Score text-to-SQL on the golden set. Spends money when live."""
    from zap.dbt_runner import dbt_target_dir
    from zap.llm.client import LlmClient
    from zap.text2sql.evaluate import evaluate, load_golden
    from zap.text2sql.schema_prompt import schema_prompt

    settings = get_settings()
    db_path = _warehouse(settings, dataset)
    manifest = dbt_target_dir(db_path, settings.dbt_project_dir) / "manifest.json"
    questions = load_golden(REPO_ROOT / "eval" / "text2sql_golden.yml")
    report = evaluate(LlmClient(settings), db_path, schema_prompt(db_path, manifest), questions)
    typer.echo(
        f"Execution accuracy {report['execution_accuracy']:.1%} on {report['questions']} "
        f"questions; {report['needed_a_repair']} needed a repair"
    )
    for level, row in report["by_level"].items():
        typer.echo(f"  {level:7} {row['correct']} of {row['questions']}")
    typer.echo(f"Report: {_save('text2sql', report)}")


@ai_app.command("agent-eval")
def agent_eval_cmd() -> None:
    """Score routing. The keyword baseline is free; the LLM router spends money when live."""
    from zap.agent.evaluate import evaluate_routing, load_golden
    from zap.llm.client import LlmClient

    questions = load_golden(REPO_ROOT / "eval" / "agent_golden.yml")
    report = evaluate_routing(LlmClient(get_settings()), questions)
    typer.echo(f"Keyword router: {report['keyword_router']['accuracy']:.1%}")
    llm = report["llm_router"]
    typer.echo(f"LLM router: {llm if isinstance(llm, str) else format(llm['accuracy'], '.1%')}")
    typer.echo(f"Report: {_save('agent_routing', report)}")


@ai_app.command("enrich")
def enrich_cmd(dataset: DatasetOpt = "full") -> None:
    """Label each distinct review text once. Spends money when live."""
    from zap.enrich.enrich_reviews import enrich
    from zap.llm.client import LlmClient

    settings = get_settings()
    report = enrich(LlmClient(settings), _warehouse(settings, dataset))
    typer.echo(
        f"{report['distinct_texts']} texts for {report['reviews']:,} reviews; agreement with "
        f"the rating: {report['agreement_with_rating_reviews']:.1%}"
    )
    typer.echo(f"Report: {_save('enrichment', report)}")
