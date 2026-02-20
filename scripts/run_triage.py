#!/usr/bin/env python3
"""Run the RLM PR triage analysis."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_repo_intel.config import load_config
from rlm_repo_intel.pipeline.rlm_session import create_frontier_rlm


def main():
    config = load_config("rlm-repo-intel.yaml")
    print("Creating frontier RLM...")
    rlm = create_frontier_rlm(config)
    
    prompt = "Analyze all open PRs. Score each for urgency and quality, assign a state, and produce the full triage JSON."
    
    print(f"Running RLM with prompt: {prompt}")
    print("=" * 80)
    
    result = rlm.run(prompt)
    
    print("=" * 80)
    print("RLM RESULT:")
    print(result)
    
    # Save results
    output_path = Path(".rlm-repo-intel/results/triage.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        output_path.write_text(json.dumps(parsed, indent=2))
    except (json.JSONDecodeError, TypeError):
        output_path.write_text(str(result))
    
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
