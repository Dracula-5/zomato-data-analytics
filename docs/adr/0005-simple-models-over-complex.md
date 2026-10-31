# ADR 5: Simple models, because the data can't reward complex ones

**Status:** accepted

## Context

A portfolio project is tempted to use the most impressive model available. The plan offered a graph
neural network for recommendations, gradient boosting for forecasting, and a reranker for search.
Each one has to earn its complexity against a baseline.

## Decision

Build the simple version first, measure it, and keep something more complex only when the
measurement justifies it. Before trusting any model, estimate what the data allows.

## Consequences

**Forecasting: ETS, not LightGBM.** ETS sits on the noise floor: if daily orders are Poisson, even
a perfect forecast scores about the same. LightGBM can't win here however well it's tuned, because
the series is steady growth, a fixed weekly shape and noise. Further tuning would only fit noise.

**Recommendations: no graph neural network.** Repeat orders, city loyalty and cuisine loyalty are
all identical to shuffled data. Matrix factorization looks 15 times better than random, but that
turned out to be popularity: it scores about 6 times random on shuffled orders that have no taste
in them, and the real and shuffled results overlap. On planted taste the same code scores far
higher, so the method works and the data has no taste to learn.

**Search: no reranker.** Hybrid retrieval ranks best on the golden questions. The reranker added
2.2 seconds a question on CPU, taking a search from 0.05 s to 2.26 s, and didn't improve on it.

**Bad:** the project shows fewer fashionable techniques. The trade is worth it, because being able
to explain why a model isn't needed is rarer than being able to fit one.

## Alternatives

- **Build the graph neural network anyway:** rejected in the plan as decision D2. Any score would
  have been noise presented as a result.
- **Tune until something wins:** that would measure the test set, not the model.
