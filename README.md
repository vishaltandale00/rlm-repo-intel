# rlm-repo-intel

**Recursive Language Model-powered repository intelligence.** Analyze any GitHub repository's codebase, PRs, and issues using RLM recursive decomposition to build deep understanding at a fraction of the cost of brute-force approaches.

## What it does

1. **Builds a codebase graph** — Recursively decomposes a repository into modules, files, symbols, and contracts using RLM. The model decides how to explore, not a fixed heuristic.
2. **Evaluates PRs in context** — Scores every PR against the codebase understanding, not in isolation. Finds redundancies, conflicts, and ranks by strategic value.
3. **Maps the issue landscape** — Connects issues to codebase areas, links PRs to issues, identifies gaps.
4. **Publishes results** — Outputs structured analysis as JSON, or pushes to a web dashboard.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    REPL Environment                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ codebase │  │   prs    │  │  issues  │  │  graph  │ │
│  │  (var)   │  │  (var)   │  │  (var)   │  │  (var)  │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
└─────────────────────────┬───────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │    Root Model          │
              │  (Sonnet/GPT-5/etc)   │
              │  Writes code to:       │
              │  - explore codebase    │
              │  - decompose modules   │
              │  - evaluate PRs        │
              │  - synthesize results  │
              └───────────┬───────────┘
                          │ recursive sub-calls
              ┌───────────┴───────────┐
              │   Worker Models        │
              │  (Haiku/Mini/Gemini)   │
              │  Handle chunks:        │
              │  - module analysis     │
              │  - PR scoring          │
              │  - pair comparison     │
              └───────────────────────┘
```

## Quick Start

```bash
# Install
pip install -e .

# Set up API keys (at least one provider)
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...

# Ingest a repository
rlm-repo-intel ingest --repo openclaw/openclaw

# Build codebase model
rlm-repo-intel model --root-model claude-sonnet-4-20250514 --worker-model claude-haiku

# Evaluate PRs
rlm-repo-intel evaluate-prs --budget 100

# Cross-PR synthesis
rlm-repo-intel synthesize --top-n 200

# Export results
rlm-repo-intel export --format json --output results/
```

## Configuration

```yaml
# rlm-repo-intel.yaml
repo:
  owner: openclaw
  name: openclaw
  branch: main

models:
  root: claude-sonnet-4-20250514      # orchestrator — best judgment
  code_worker: codex-5.3        # code understanding
  reasoning_worker: gemini-3.1-pro  # broad reasoning
  cheap_worker: claude-haiku    # bulk processing

budget:
  max_spend_usd: 200
  phase1_pct: 20   # codebase modeling
  phase2_pct: 45   # PR evaluation
  phase3_pct: 35   # synthesis + issues

output:
  format: json
  push_to: https://clawmrades.ai/api  # optional
```

## How it works

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full recursive decomposition strategy, cost model, and implementation details.

## Based on

- [Recursive Language Models](https://arxiv.org/abs/2512.24601) (Zhang, Kraska, Khattab 2025)
- [`rlms` library](https://github.com/alexzhang13/rlm)

## License

MIT
