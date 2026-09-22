export interface HolisticDimension {
  score: number;
  summary: string;
}

export interface HolisticCompetencyDimensions {
  technical_depth?: HolisticDimension;
  practical_experience?: HolisticDimension;
  problem_solving?: HolisticDimension;
  communication_clarity?: HolisticDimension;
}

export interface HolisticCompetency {
  score: number;
  dimensions: HolisticCompetencyDimensions;
}

export interface QuestionAnalysisItem {
  pair_id?: string;
  qa_id: string;
  question: string;
  answer: string;
  accuracy_score: number | null;
  observations?: string;
}

export interface JDAnalysis {
  covered_skills?: string[];
  remaining_skills?: string[];
  summary?: string;
}

export interface ResumeValidation {
  verified_projects?: string[];
  unverified_projects?: string[];
  summary?: string;
}

export interface ExecutiveSummary {
  overall_score: number | null;
  qa_accuracy_average: number | null;
  holistic_competency_score: number | null;
  qa_evaluated_count?: number;
  score_status?: 'complete' | 'insufficient_evidence' | string;
}

export interface EvidenceData {
  transcript?: any[];
  confirmed_qa_pairs?: any[];
}

export interface CopilotFinalReport {
  session_id: string;
  evaluated_at?: string;
  is_finalized: boolean;
  executive_summary?: ExecutiveSummary;
  holistic_competency?: HolisticCompetency | null;
  qa_accuracy_average: number | null;
  overall_score: number | null;
  qa_evaluated_count?: number;
  total_qa_pairs?: number;
  scoring_formula?: string;
  confirmed_qa_pairs?: QuestionAnalysisItem[];
  question_analysis?: QuestionAnalysisItem[];
  strengths?: string[];
  development_areas?: string[];
  jd_analysis?: JDAnalysis;
  resume_validation?: ResumeValidation;
  conversation_summary?: string;
  observer_notes?: string[];
  evidence?: EvidenceData;
  transcript?: any[];
  intelligence?: any;
  assistance?: any;
  error?: string;
}
