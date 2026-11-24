/** A saved server case -> the workspace it opens into (docs/followup-plan.md §A.4, T116). Pure functions.
 * Turns are placed by server facts, mirroring the live routing of turnSaga.finishTurn. */
import type { CaseView, TurnView } from '.';
import type { CaseSnapshot, ChatMessage, Finding, FindingEdit, Notice, TurnItem } from '../types';
import { emptyResult, toFinding, toResult } from './adapters';
import { endTurn, reviewResolved, turnText } from '../../redux/slices/turnItems';

/** The progress list a turn showed; planning calls are not rows (turnItems.applyStep). */
export function turnItems(turn: TurnView): TurnItem[] {
  const items: TurnItem[] = turn.items.flatMap((item): TurnItem[] => {
    if (item.kind === 'text') return [{ kind: 'text', messageId: item.message_id, text: item.text }];
    return item.step.key === 'tool:write_todos' ? [] : [{ kind: 'step', step: item.step, state: item.step.status }];
  });
  return turn.outcome ? endTurn(items, turn.outcome) : items;
}

/** The clinician's edits as the server applied them, so the next POST /findings sends the whole set again. */
export function editsFromFindings(findings: Finding[]): Record<string, FindingEdit> {
  const edits: Record<string, FindingEdit> = {};
  for (const finding of findings) {
    const edit: FindingEdit = { finding_id: finding.id };
    if (finding.excluded) edit.excluded = true;
    for (const field of ['assertion', 'assessment', 'subject'] as const) if (field in finding.machine) Object.assign(edit, { [field]: finding[field] });
    if ('term' in finding.machine && finding.term) edit.hpo_id = finding.term.id;
    if (Object.keys(edit).length > 1) edits[finding.id] = edit;
  }
  return edits;
}

export function snapshotFromView(view: CaseView): CaseSnapshot {
  const id = view.case_id;
  const turns = view.turns ?? [];
  const findings = view.findings.map(toFinding);
  const messages = turns.filter(t => t.route === 'message');
  // The description turn: the last message that ran the analysis, else the first message (it came from the description box).
  const descriptionTurn = messages.filter(t => t.outcome === 'review').at(-1) ?? messages[0] ?? null;
  const later = descriptionTurn ? turns.filter(t => t.turn_no > descriptionTurn.turn_no) : [];
  const progressTurn = later.filter(t => t.route === 'review' && t.input.action === 'remap').at(-1) ?? descriptionTurn;
  const summaryTurn = later.filter(t => t.route === 'review' && t.input.action === 'continue' && t.outcome === 'done').at(-1);
  // A card edit that changed the list hides the summary written before it (P7).
  const summaryCurrent = Boolean(summaryTurn) && !turns.some(t => t.route === 'findings' && t.turn_no > summaryTurn!.turn_no && t.input.ranking_changed);

  // The server's description: a chat message is added to it rather than replacing it (T137).
  const description = view.description ?? descriptionTurn?.input.text ?? '';
  const analysisRun = descriptionTurn ? { id: `${id}:${descriptionTurn.turn_no}`, description, completed: descriptionTurn.outcome === 'review' || descriptionTurn.outcome === 'done' } : null;
  const meta = { analysisId: analysisRun?.id ?? id, description };
  let result = view.ranking ? toResult(view.ranking, findings, meta) : summaryTurn ? emptyResult(findings, meta, view.hpo_release) : null;
  if (result) {
    const summary = summaryTurn ? turnText(turnItems(summaryTurn)) : '';
    result = { ...result, summary, rankingVersion: 1, summaryFor: summaryCurrent ? 1 : null };
  }

  const chat: ChatMessage[] = messages.filter(t => t !== descriptionTurn).flatMap(t => {
    const progress = turnItems(t);
    return [
      { id: `${id}:${t.turn_no}`, role: 'user' as const, text: t.input.text ?? '', citations: [], progress: [] },
      { id: `${id}:${t.turn_no}-a`, role: 'assistant' as const, text: turnText(progress), citations: t.citations, progress },
    ];
  });

  let notice: Notice = null;
  if (view.analysis_flags?.split_failed) notice = 'splitFailed';
  else if (descriptionTurn?.outcome === 'done' && !findings.length) notice = 'notAnalyzed';
  else if (descriptionTurn?.outcome === 'review' && !findings.length) notice = 'noFindings';

  return {
    id, title: view.title ?? '', description,
    findings, analysisRun, hasAnalyzed: Boolean(descriptionTurn), result, notice,
    edits: editsFromFindings(findings), chat, reviewPending: view.status === 'review',
    // A review that is no longer pending was resolved: "Chờ bác sĩ xem biểu hiện" reads as done (T134 (5)).
    reviewProgress: progressTurn ? (view.status === 'review' ? turnItems(progressTurn) : reviewResolved(turnItems(progressTurn))) : [],
    notesArchive: (view.notes_archive ?? []).map((entry, index) => ({ analysisId: `${id}:archive:${index}`, description: entry.description, findings: entry.findings.map(toFinding) })),
  };
}
