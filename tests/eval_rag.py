"""
RAG evaluation — faithfulness, contextual relevancy, answer relevancy.

Tests FAQ questions against the knowledge base:
  1. Retrieves context via search_knowledge()
  2. Generates answer via faq_node
  3. Scores with DeepEval metrics (or falls back to LLM-as-judge)

Usage:
  cd cno_ivr
  venv/Scripts/python tests/eval_rag.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK   = "\033[92m"
FAIL = "\033[91m"
BOLD = "\033[1m"
DIM  = "\033[90m"
CYAN = "\033[96m"
RESET = "\033[0m"

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "eval_results")

RAG_TEST_SET = [
    {
        "question": "What is a whole life insurance policy?",
        "expected_keywords": ["whole life", "permanent", "coverage", "cash value"],
    },
    {
        "question": "What is the grace period for a missed payment?",
        "expected_keywords": ["grace period", "days", "payment"],
    },
    {
        "question": "How does the cash value of a policy grow?",
        "expected_keywords": ["cash value", "grow", "interest"],
    },
    {
        "question": "What happens if I stop paying my premiums?",
        "expected_keywords": ["lapse", "premium", "stop", "nonforfeiture"],
    },
    {
        "question": "Can I take a loan against my life insurance policy?",
        "expected_keywords": ["loan", "borrow", "cash value", "interest"],
    },
    {
        "question": "What is term life insurance?",
        "expected_keywords": ["term", "period", "coverage", "expires"],
    },
    {
        "question": "How do I change my beneficiary?",
        "expected_keywords": ["beneficiary", "change", "form", "request"],
    },
    {
        "question": "What documents do I need for a claim?",
        "expected_keywords": ["claim", "death certificate", "form"],
    },
]


async def evaluate_rag_question(question: str, expected_keywords: list) -> dict:
    """Evaluate a single RAG question for faithfulness and relevancy."""
    from services.rag import search_knowledge
    from config import settings

    # Step 1: Retrieve context
    context = ""
    if settings.enable_rag:
        try:
            context = await search_knowledge(question, k=3)
        except Exception:
            pass

    # Step 2: Generate answer via LLM (same as faq_node)
    answer = ""
    if context:
        from langchain_groq import ChatGroq
        from langchain_core.messages import SystemMessage, HumanMessage
        from core.prompts.system_prompt import CNO_SYSTEM_PROMPT

        llm = ChatGroq(
            model=settings.groq_model,
            temperature=0.4,
            api_key=settings.groq_api_key,
            max_tokens=200,
        )
        grounding = "Use ONLY the following information to answer.\n\nContext:\n" + context
        response = await llm.ainvoke([
            SystemMessage(content=CNO_SYSTEM_PROMPT + "\n\n" + grounding),
            HumanMessage(content=f"Answer this caller question in 1-2 sentences for voice: {question}"),
        ])
        answer = response.content.strip()

    # Step 3: Score
    scores = {}

    # Faithfulness: is the answer grounded in the context?
    if context and answer:
        scores["faithfulness"] = await _score_faithfulness(question, context, answer)
    else:
        scores["faithfulness"] = {"score": 0.0, "reason": "No context retrieved"}

    # Contextual relevancy: is the context relevant to the question?
    if context:
        scores["contextual_relevancy"] = await _score_contextual_relevancy(question, context)
    else:
        scores["contextual_relevancy"] = {"score": 0.0, "reason": "No context retrieved"}

    # Answer relevancy: does the answer address the question?
    if answer:
        scores["answer_relevancy"] = await _score_answer_relevancy(question, answer)
    else:
        scores["answer_relevancy"] = {"score": 0.0, "reason": "No answer generated"}

    # Keyword coverage (simple heuristic check)
    if answer:
        answer_lower = answer.lower()
        found = [kw for kw in expected_keywords if kw.lower() in answer_lower]
        scores["keyword_coverage"] = {
            "score": round(len(found) / len(expected_keywords), 2) if expected_keywords else 1.0,
            "found": found,
            "missing": [kw for kw in expected_keywords if kw.lower() not in answer_lower],
        }
    else:
        scores["keyword_coverage"] = {"score": 0.0, "found": [], "missing": expected_keywords}

    return {
        "question": question,
        "context_length": len(context),
        "context_chunks": len(context.split("\n\n")) if context else 0,
        "answer": answer,
        "scores": scores,
    }


async def _score_faithfulness(question: str, context: str, answer: str) -> dict:
    """Score whether the answer is grounded in the retrieved context."""
    return await _llm_score(
        f"Question: {question}\n\nContext:\n{context}\n\nAnswer: {answer}",
        "Rate 0.0-1.0: Is the answer fully grounded in the provided context? "
        "1.0 means every claim in the answer is supported by the context. "
        "0.0 means the answer is completely hallucinated."
    )


async def _score_contextual_relevancy(question: str, context: str) -> dict:
    """Score whether the retrieved context is relevant to the question."""
    return await _llm_score(
        f"Question: {question}\n\nRetrieved Context:\n{context}",
        "Rate 0.0-1.0: How relevant is the retrieved context to answering the question? "
        "1.0 means perfectly relevant. 0.0 means completely irrelevant."
    )


async def _score_answer_relevancy(question: str, answer: str) -> dict:
    """Score whether the answer addresses the question."""
    return await _llm_score(
        f"Question: {question}\n\nAnswer: {answer}",
        "Rate 0.0-1.0: Does the answer directly address the question? "
        "1.0 means it fully answers the question. 0.0 means it's completely off-topic."
    )


async def _llm_score(content: str, criterion: str) -> dict:
    """Use Groq LLM as judge to score on a 0-1 scale."""
    from langchain_groq import ChatGroq
    from langchain_core.messages import SystemMessage, HumanMessage
    from config import settings

    llm = ChatGroq(
        model=settings.groq_model,
        temperature=0,
        api_key=settings.groq_api_key,
    )

    prompt = f"""{content}

Evaluation: {criterion}

Respond with ONLY a JSON object: {{"score": <0.0-1.0>, "reason": "<brief explanation>"}}"""

    try:
        response = await llm.ainvoke([
            SystemMessage(content="You are an RAG evaluation judge. Respond only with valid JSON."),
            HumanMessage(content=prompt),
        ])
        text = response.content.strip()
        if "{" in text:
            json_str = text[text.index("{"):text.rindex("}") + 1]
            parsed = json.loads(json_str)
            return {"score": float(parsed.get("score", 0.5)), "reason": parsed.get("reason", "")}
    except Exception as e:
        return {"score": 0.5, "reason": f"Error: {str(e)[:80]}"}

    return {"score": 0.5, "reason": "Could not parse response"}


async def run_eval():
    print(f"\n{BOLD}RAG Evaluation — Faithfulness, Relevancy, Answer Quality{RESET}")
    print(f"{DIM}{'=' * 70}{RESET}")
    print(f"Test questions: {len(RAG_TEST_SET)}\n")

    all_results = []

    for tc in RAG_TEST_SET:
        question = tc["question"]
        print(f"\n{CYAN}Q: {question}{RESET}")

        result = await evaluate_rag_question(question, tc["expected_keywords"])
        all_results.append(result)

        if result["answer"]:
            print(f"  A: {result['answer'][:100]}{'...' if len(result['answer']) > 100 else ''}")
        else:
            print(f"  {FAIL}No answer generated{RESET}")

        print(f"  Context: {result['context_chunks']} chunks, {result['context_length']} chars")

        for metric_name, metric_data in result["scores"].items():
            score = metric_data.get("score", 0)
            if isinstance(score, float) and score <= 1.0:
                color = OK if score >= 0.8 else (FAIL if score < 0.5 else CYAN)
                print(f"    {metric_name:<25} {color}{score:.2f}{RESET}", end="")
            else:
                print(f"    {metric_name:<25} {score}", end="")
            reason = metric_data.get("reason", "")
            if reason:
                print(f"  {DIM}{reason[:50]}{RESET}")
            else:
                print()

    # Aggregate scores
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}Aggregate RAG Metrics{RESET}\n")

    metrics = ["faithfulness", "contextual_relevancy", "answer_relevancy"]
    for metric in metrics:
        scores = [r["scores"][metric]["score"] for r in all_results
                  if metric in r["scores"] and isinstance(r["scores"][metric].get("score"), (int, float))]
        if scores:
            avg = sum(scores) / len(scores)
            color = OK if avg >= 0.8 else (FAIL if avg < 0.5 else CYAN)
            print(f"  {metric:<25} {color}{avg:.3f}{RESET} (n={len(scores)})")

    # Keyword coverage
    kw_scores = [r["scores"]["keyword_coverage"]["score"] for r in all_results
                 if "keyword_coverage" in r["scores"]]
    if kw_scores:
        avg_kw = sum(kw_scores) / len(kw_scores)
        color = OK if avg_kw >= 0.7 else FAIL
        print(f"  {'keyword_coverage':<25} {color}{avg_kw:.3f}{RESET}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    summary = {
        "metrics": {
            metric: {
                "avg": round(sum(r["scores"][metric]["score"] for r in all_results
                                 if metric in r["scores"] and isinstance(r["scores"][metric].get("score"), (int, float)))
                             / max(1, len([r for r in all_results
                                           if metric in r["scores"] and isinstance(r["scores"][metric].get("score"), (int, float))])), 3)
            }
            for metric in metrics
        },
        "questions": all_results,
    }
    results_path = os.path.join(RESULTS_DIR, "rag_eval.json")
    with open(results_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n{DIM}Results saved to {results_path}{RESET}")

    return summary


if __name__ == "__main__":
    results = asyncio.run(run_eval())
    faith = results.get("metrics", {}).get("faithfulness", {}).get("avg", 0)
    sys.exit(0 if faith >= 0.7 else 1)
