export interface AgentTraces {
  code_analyst?: { reasoning?: string; risk_score?: number; quality_score?: number };
  codebase_expert?: { reasoning?: string; strategic_value?: number; novelty_score?: number };
  risk_assessor?: { reasoning?: string; risk_score?: number };
  adversarial_reviewer?: {
    reasoning?: string;
    rejection_confidence?: number;
    counter_arguments?: string[];
  };
  synthesizer?: { synthesis_reasoning?: string };
  disagreement_points?: string[];
}

export interface EvaluationItem {
  pr_number: number;
  title: string;
  state?: "ready" | "needs_author_review" | "triage" | string;
  author?: string;
  urgency?: number;
  quality?: number;
  justification?: string;
  key_risks?: string[] | string;
  verdict?: string;
  evidence?: string[] | string;
  risk_score?: number;
  quality_score?: number;
  strategic_value?: number;
  final_rank_score?: number;
  final_score?: number;
  review_summary?: string;
  confidence?: number;
  impact_scope?: string[];
  novelty_score?: number;
  agent_traces?: AgentTraces;
}

export interface RankingData {
  ranking?: Array<{ number: number; rank: number; reason: string }>;
}

export interface ClusterRelation {
  pr_a: number;
  pr_b: number;
  relation_type: string;
  explanation?: string;
  proposer_reasoning?: string;
  challenger_reasoning?: string;
  debate?: {
    proposer?: string;
    challenger?: string;
  };
}

export interface ClusterItem {
  cluster_id: number;
  members: number[];
  size: number;
  relations: ClusterRelation[];
}

export interface SummaryData {
  total_prs_evaluated: number;
  total_modules: number;
  clusters: number;
  themes: string[];
  last_updated?: string;
  phase?: string;
  current_phase?: string;
  cost_estimate?: number;
  cost_estimate_usd?: number;
}

export interface AgentTraceStep {
  iteration: number;
  type: "llm_response" | "code_execution";
  content: string;
  timestamp: string;
}

export interface RunMeta {
  id: string;
  timestamp: string;
  status?: "running" | "completed" | "failed" | "archived";
  kind?: "baseline" | "experimental" | "ab_control" | "ab_treatment";
  prompt_version?: string;
  prompt_hash?: string;
  prompt_label?: string;
  model_name?: string;
  model_root?: string;
  budget?: number;
  start_time?: string;
  started_at?: string;
  end_time?: string;
  ended_at?: string;
  time_elapsed_seconds?: number;
  token_input?: number;
  token_output?: number;
  total_tokens?: number;
  tokens_used?: number;
  cost_usd?: number;
  cost?: number;
  total_cost_usd?: number;
  total_prs_seen?: number;
  total_prs_scored?: number;
}
