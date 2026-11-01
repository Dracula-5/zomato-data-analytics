# Evaluation

What the AI parts scored, what the misses were, and what these numbers can't tell you. Every score
was read before it was quoted, misses first. The figures also appear in [METRICS.md](METRICS.md),
which is generated from the run files.

This file isn't part of what search reads, and a test keeps it that way. The reason is below.

## Summary

| What | Score | Out of | Read it as |
|---|---|---|---|
| Text-to-SQL, execution accuracy | 88.9% | 36 questions | Strict. Two of the four misses were correct answers |
| Answers supported by their passages | 86.0% | 50 answers | Supported, which isn't the same as true |
| Answers citing the right evidence | 85.2% | 54 questions | The check that catches what the line above misses |
| Unanswerable questions declined | 6 of 6 | 6 questions | It also declined 4 it could have answered |
| Routing, LLM | 96.8% | 31 questions | 74.2% for the keyword baseline |
| Sentiment agreement with ratings | 100% | 294 distinct texts | Says little: the texts are templates |

Model: `gpt-5.6-luna`, priced from OpenAI's own page. All of it, re-runs included, cost about $0.10
of a $2 budget. I wrote the questions and the documents, which flatters every score here. These are
small sets too: one question is about 2.8 points of text-to-SQL accuracy.

## Search

Measured over 173 chunks and 54 golden questions, for free and on a laptop.

| Stage | Recall@5 | MRR | Recall@5, exact | Recall@5, paraphrased |
|---|---|---|---|---|
| BM25 | 0.685 | 0.523 | 0.889 | 0.583 |
| Dense | 0.815 | 0.648 | 0.944 | 0.750 |
| Hybrid | 0.852 | 0.737 | 1.000 | 0.778 |
| Hybrid + rerank | 0.889 | 0.701 | 0.944 | 0.861 |

Hybrid ranks best and is the only stage to find every exact identifier, which matters for rule
names. Answers use it.

The reranker didn't earn its place. On top of a search that takes about 0.1 s, it added 2.2 to 4.0
seconds a question on CPU across runs, depending on what else the laptop was doing. It also lowered
MRR and lost one exact identifier. Its higher recall is two questions of 54, which isn't a real
difference.

## Text-to-SQL

32 of 36 correct, and every query ran. A question counts as correct only if its result rows match
the gold query's. By level: easy 12 of 12, medium 11 of 13, hard 9 of 11.

| Miss | What happened | Verdict |
|---|---|---|
| Weekend vs weekday orders | Same two numbers, labelled Weekend and Weekday, not true and false | Correct, scorer too strict |
| Top 5 restaurants in Bangalore | Same five and same GMV, with names rather than IDs | Correct, scorer too strict |
| Top 5 dishes by units | Grouped by dish ID, so Jeera Rice appeared twice with its total split | Real error |
| Average sale of all-veg orders | Grouped by order: 5.4 million rows, not one average | Real error |

The score stays at 88.9%. Rewriting the gold set after seeing the results could only raise it, and
that habit is how a test stops meaning anything. Both real errors are grouping mistakes on hard
questions, so that's where to look first in a real deployment.

## Answers

It answered 50 of the 54 questions and declined the other 4, as well as all 6 that have no answer.
A separate judge reads each answer against its passages and lists any claim they don't support. It
rejected 7 of the 50.

- Four came from the right passages and went slightly too far, such as calling a value the
  "default" when the passage didn't.
- Two went beyond passages that didn't hold the evidence.
- One was simply wrong. Asked whether customer logins could end up in cloud storage, the model said
  yes. The password is dropped at ingestion, but that passage wasn't among those it used.

I didn't tune the prompts on these questions, because that would turn the test into training data.

### Faithful doesn't mean true

The judge only compares an answer with the passages it was given. In an earlier run the same wrong
"yes" came back with different passages, and the judge passed it: the answer was faithful to what
was retrieved, and those passages were the wrong ones.

So the two checks do different jobs. Faithfulness asks whether the answer stuck to its sources.
Citing the evidence asks whether those were the right sources, and that check is deterministic,
which is why it flagged the answer on both runs.

### Don't publish results where the system can read them

The results first went into `LEARNING_NOTES.md`, which search reads. The next run moved: hybrid
recall fell to 0.815 and faithfulness to 84.0%, because commentary about the test was now in the
index, competing with the passages that answer it. Every write-up also changed the scores it was
reporting.

So results moved here, a test now fails if they go back, and everything was re-measured on the clean
documents. The scores above are from that run.

## Routing

The LLM got 30 of 31, against 23 of 31 for keywords, for less than a cent in total. Only one
question fooled it: "How is GMV trending month by month in 2026?" looked like a forecast. Keywords
got that one wrong too, and that question stays in the set.

## Sentiment

100% agreement sounds good and isn't saying much. It isn't circular, as the model sees the text and
never the rating. But the 300,000 reviews are 294 templated sentences with obvious sentiment. This
shows the pipeline works, and says nothing about real reviews. Labelling each text once saved
299,706 calls, and for the same reason the cost per review wouldn't carry over to real data.

## What would make these stronger

- Questions written by someone who didn't write the documents.
- Real reviews, with real ambiguity in them.
- A gold set agreed before the first run, including how to treat labels and ID columns.
