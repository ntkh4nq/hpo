import { useTranslation } from 'react-i18next';
import { useAppSelector } from '../../redux';
import { selectAnalysis, selectEdits } from '../../redux/selectors';
import { Icon } from '../../shared/ui';
import { applyEdit, awaitsDecision, isEdited, plainTitle } from '../../shared/api/adapters';
import type { Finding } from '../../shared/types';

type Group = 'measure' | 'attention' | 'present' | 'unsure' | 'absent';
const GROUPS: Group[] = ['measure', 'attention', 'present', 'unsure', 'absent'];

/** A measurement nobody concluded on waits for the clinician (T117); then: needs a term first; then by what the description asserts. */
function groupOf(finding: Finding): Group {
  if (awaitsDecision(finding)) return 'measure';
  if (finding.measurement && finding.assertion === 'ABSENT') return 'absent';   // read as normal: no term needed
  if (finding.mappingStatus !== 'MAPPED') return 'attention';
  if (finding.assertion === 'PRESENT') return 'present';
  if (finding.assertion === 'ABSENT') return 'absent';
  return 'unsure';
}

/** Findings as one compact row each, grouped by status; a row opens FindingDialog. */
export function FindingList({ findings, onOpen, id }: { findings: Finding[]; onOpen: (id: string) => void; id?: string }) {
  const { t } = useTranslation();
  const edits = useAppSelector(selectEdits);
  const shown = findings.map(finding => ({ server: finding, finding: applyEdit(finding, edits[finding.id]) }));
  // An AMBIGUOUS finding the ranking used through the shared concept of its options says so (T134 (2)).
  const terms = useAppSelector(selectAnalysis).result?.terms ?? [];
  const sharedConcept = (id: string) => terms.find(term => term.fromOptions.length > 0 && term.findingIds.includes(id))?.label;

  return (
    <div className="finding-list" id={id}>
      {GROUPS.map(group => {
        const rows = shown.filter(row => groupOf(row.finding) === group);
        if (!rows.length) return null;
        return (
          <section key={group} className="finding-list__group" data-group={group} aria-label={t(`phenotype.group.${group}`)}>
            <h3 className="finding-list__group-title">{t(`phenotype.group.${group}`)} <span className="finding-list__count">{rows.length}</span></h3>
            <ul className="finding-list__rows">
              {rows.map(({ server, finding }) => {
                // The state is never left out when it is not "có": the row's name may be a positive rewording (T129).
                const marks = [
                  finding.assertion !== 'PRESENT' && group !== 'measure' ? t(`assertion.${finding.assertion}`) : null,
                  finding.subject !== 'FETUS' ? t(`subject.${finding.subject}`) : null,
                  finding.assessment !== 'ASSESSED' ? t(`assessment.${finding.assessment}`) : null,
                  finding.excluded ? t('finding.excludedShort') : null,
                  finding.note?.text ? t('finding.hasNote') : null,
                  isEdited(server, edits[server.id]) ? t('finding.edited') : null,
                ].filter(Boolean);
                const shared = finding.mappingStatus === 'AMBIGUOUS' ? sharedConcept(finding.id) : undefined;
                const plain = plainTitle(finding);
                return (
                  <li key={finding.id}>
                    <button type="button" className="finding-row" data-group={group} onClick={() => onOpen(finding.id)}>
                      <span className="finding-row__main">
                        <span className="finding-row__name">
                          {finding.term && finding.mappingStatus === 'MAPPED' ? finding.term.label : plain.name}
                          {finding.measurement && <span className="finding-row__value">{finding.measurement.value}</span>}
                        </span>
                        {shared && <span className="finding-row__shared">{t('finding.sharedConcept', { label: shared })}</span>}
                        <span className="finding-row__quote">“{finding.sourceText}”</span>
                      </span>
                      {marks.length > 0 && <span className="finding-row__marks">{marks.join(' · ')}</span>}
                      <span className="finding-row__chevron"><Icon name="chevron" size={16}/></span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
