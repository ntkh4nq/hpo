export type Locale = 'en' | 'vi';
export type Localized = Record<Locale, string>;
export type Scenario = 'skeletal' | 'prenatal' | 'ambiguous' | 'empty' | 'insufficient' | 'failure';
export type Assertion = 'PRESENT' | 'ABSENT' | 'SUSPECTED' | 'UNCERTAIN';
export type Assessment = 'ASSESSED' | 'LIMITED' | 'NOT_ASSESSED' | 'NOT_ASSESSABLE';
export type MappingStatus = 'MAPPED' | 'AMBIGUOUS' | 'UNMAPPED';
export type Subject = 'FETUS' | 'MOTHER' | 'FAMILY';
/** label = HPO English name, the only name shown (T120); definition = English definition. */
export interface HpoTerm { id: string; label: string; definition: string }
/** A measured value written in the description, both parts verbatim (T117). */
export interface Measurement { quantity: string; value: string }
export interface Finding {
  id: string; phrase: string; sourceText: string; sourceSpan: [number, number] | null; subject: Subject;
  measurement: Measurement | null;
  term: HpoTerm | null; options: HpoTerm[]; candidates: HpoTerm[];
  assertion: Assertion; assessment: Assessment; mappingStatus: MappingStatus; temporality: string | null;
  excluded: boolean;
  /** Machine value of each field the clinician changed (backend FindingView.machine). */
  machine: Record<string, unknown>;
  note?: { text: string };
}
/** One clinician edit, exactly as the API takes it (agent/state.py: FindingEdit); omitted = unchanged. */
export interface FindingEdit { finding_id: string; excluded?: boolean; assertion?: Assertion; assessment?: Assessment; subject?: Subject; hpo_id?: string }
export interface AnalysisRun { id: string; description: string; completed: boolean }
export interface ArchivedAnalysisNotes { analysisId: string; description: string; findings: Finding[] }
export interface Evidence { id: string; title: string; excerpt: string; location: string; url?: string; active?: boolean; citation?: string; license?: string }
export type PairStatus = 'cited' | 'no_passage' | 'no_supporting_passage' | 'failed';
export interface ExplanationPair { queryHpoId: string; diseaseHpoId: string; status: PairStatus; claims: { text: string; refs: string[] }[] }
/** The literature part of a candidate's explanation; the code-built parts come from matches/genes/unmentioned. */
export interface CandidateExplanation { pairs: ExplanationPair[] }
export type MatchKind = 'excluded' | 'exact' | 'disease_more_specific' | 'disease_more_general' | 'related' | 'none';
export interface MatchView {
  queryHpoId: string; findingIds: string[]; assertion: 'PRESENT' | 'SUSPECTED'; kind: MatchKind;
  diseaseHpoId: string | null; commonAncestorId: string | null; frequencyRaw: (string | null)[]; references: string[][];
}
export interface CandidateGene { symbol: string; ncbiGeneId: string; associationType: string | null }
export interface UnmentionedTerm { hpoId: string; labelEn: string; frequencyRaw: (string | null)[]; references: string[][] }
/** A non-phenotype annotation of the disease (HPO aspect I: inheritance, C: clinical course), with its sources. */
/** The fetus was assessed not to have this; the disease records it frequently (T130). */
export interface ConflictView { queryHpoIds: string[]; findingIds: string[]; diseaseHpoId: string; diseaseLabel: string; frequencyRaw: (string | null)[]; references: string[][] }
export interface DiseaseTrait { hpoId: string; labelEn: string; frequencyRaw: (string | null)[]; references: string[][] }
/** A suggested disease. Rank is shown, the score never is (principle #2). */
export interface Candidate {
  id: string; name: string; sourceDb: string; rank: number; genes: CandidateGene[];
  matchedFindingIds: string[]; matches: MatchView[]; unmentioned: UnmentionedTerm[];
  inheritance: DiseaseTrait[]; clinicalCourse: DiseaseTrait[]; conflicts: ConflictView[];
  evidenceIds: string[]; explanation: CandidateExplanation | null;
  /** Evidence id of the disease's overview passage (an Orphanet definition), shown once at the top of its details (T110). */
  overviewId: string | null;
}
export interface SkippedFinding { phrase: string | null; hpoId: string | null; reason: string; findingId: string | null; assertion: string | null }
/** An HPO term the ranking used, with the findings behind it (ranking.terms, T129). */
export interface UsedTerm { hpoId: string; label: string; findingIds: string[]; fromOptions: string[]; assertion: 'PRESENT' | 'SUSPECTED' }
export type ExplanationsState = 'idle' | 'loading' | 'ready' | 'unavailable' | 'error';
export interface SuggestionResult {
  analysisId: string; description: string; profile: Finding[]; summary: string; candidates: Candidate[]; evidence: Evidence[]; insufficient: boolean;
  skipped: SkippedFinding[]; terms: UsedTerm[]; hpoRelease: string;
  /** Bumped by every new supervisor ranking and by every card re-rank that changes the suggestions. */
  rankingVersion: number;
  /** rankingVersion the supervisor's summary describes (P7); the summary shows only while it matches. */
  summaryFor: number | null;
  explanations: ExplanationsState;
}
export type CitationKind = 'hpo' | 'disease' | 'pmid' | 'chunk';
export interface Citation { id: string; kind: CitationKind | null; label: string | null }
export type StepStatus = 'running' | 'done' | 'error';
/** SSE `step` data (api/stream.py: Steps). */
export interface Step {
  id: string; parent_id: string | null; kind: 'todo' | 'tool' | 'node'; key: string; status: StepStatus;
  label: string | null; params: Record<string, unknown> | null; summary: Record<string, number> | null;
}
export type TurnItem =
  | { kind: 'step'; step: Step; state: StepStatus | 'waiting' }
  | { kind: 'text'; messageId: string; text: string };
export interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string; citations: Citation[]; progress: TurnItem[] }
export type Notice = 'noFindings' | 'notAnalyzed' | 'caseExpired' | 'splitFailed' | null;
export interface AnalysisRequest { requestId: string; text: string }
export interface ReviewContinueRequest { requestId: string }
export interface ChatRequest { requestId: string; text: string }

/** One saved case in the sidebar list (GET /cases, T116). */
export interface CaseEntry { id: string; title: string; updatedAt: string }

/** Everything a case opens into, built from the server case (shared/api/caseFromView.ts, T116). */
export interface CaseSnapshot {
  /** Server case id (T116). */
  id: string;
  title: string;
  description: string;
  findings: Finding[];
  analysisRun: AnalysisRun | null;
  notesArchive: ArchivedAnalysisNotes[];
  hasAnalyzed: boolean;
  result: SuggestionResult | null;
  notice: Notice;
  edits: Record<string, FindingEdit>;
  chat: ChatMessage[];
  /** The server was waiting at the review step when the snapshot was taken. */
  reviewPending: boolean;
  /** Progress and answer text of the last description turn (kept so a "not analysed" answer survives a case switch). */
  reviewProgress: TurnItem[];
}
