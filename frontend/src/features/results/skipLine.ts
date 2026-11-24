import type { Finding, SkippedFinding } from '../../shared/types';

type T = (key: string, options?: Record<string, unknown>) => string;

/** "<state> · <reason>" for a finding the ranking did not use. The state always travels with the finding, since its
 *  phrase is a positive rewording ("bố mẹ cùng huyết thống" + ABSENT) (T129). Unknown reason keys show as written. */
export function skipLine(item: SkippedFinding, finding: Finding | undefined, t: T): string {
  const assertion = finding?.assertion ?? item.assertion;
  const parts = [assertion ? t(`assertion.${assertion}`) : null];
  if (finding && finding.subject !== 'FETUS' && item.reason !== 'not_fetal') parts.push(t(`subject.${finding.subject}`));
  parts.push(t(`skipReason.${item.reason}`, { defaultValue: item.reason }));
  return parts.filter(Boolean).join(' · ');
}
