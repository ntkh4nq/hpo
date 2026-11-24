import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAppDispatch, useAppSelector } from "../../redux";
import { analysisActions, workspaceActions } from "../../redux/slices";
import {
  selectAnalysis,
  selectAnalysisRun,
  selectBusy,
  selectFindings,
  selectWorkspace,
} from "../../redux/selectors";
import { Button, Panel } from "../../shared/ui";
import { sampleCases } from "../../mocks";
import type { Locale, Scenario } from "../../shared/types";

export function ClinicalInput({
  introductory = false,
  expandRequest = 0,
}: {
  introductory?: boolean;
  expandRequest?: number;
}) {
  const { t, i18n } = useTranslation();
  const locale: Locale = i18n.resolvedLanguage?.startsWith("vi") ? "vi" : "en";
  const dispatch = useAppDispatch();
  const workspace = useAppSelector(selectWorkspace);
  const findings = useAppSelector(selectFindings);
  const run = useAppSelector(selectAnalysisRun);
  const input = useRef<HTMLTextAreaElement>(null);
  const analysis = useAppSelector(selectAnalysis);
  const busy = useAppSelector(selectBusy);
  const [scenario, setScenario] = useState<Scenario>("skeletal");
  const [collapsed, setCollapsed] = useState(false);
  const [showSamples, setShowSamples] = useState(false);

  useEffect(() => {
    setCollapsed(Boolean(run?.completed));
  }, [run?.completed]);

  // Only a new request expands the editor; a request made before this panel mounted (e.g. before a case switch) does not.
  const seenRequest = useRef(expandRequest);
  const requested = expandRequest !== seenRequest.current;
  useEffect(() => {
    if (expandRequest !== seenRequest.current) setCollapsed(false);
  }, [expandRequest]);
  useLayoutEffect(() => {
    if (requested && !collapsed) {
      input.current?.focus({ preventScroll: true });
      seenRequest.current = expandRequest;
    }
  }, [collapsed, expandRequest, requested]);

  const loadSample = () => {
    dispatch(
      workspaceActions.sampleLoaded({ text: sampleCases[scenario][locale] }),
    );
    setShowSamples(false);
  };
  const analyze = () => {
    if (!workspace.description.trim() || busy) return;
    dispatch(
      analysisActions.requested({
        requestId: crypto.randomUUID(),
        text: workspace.description,
      }),
    );
  };

  if (collapsed && run?.completed) {
    return (
      <Panel className="clinical-input-panel clinical-input-panel--compact">
        <div className="patient-card-header">
          <Button
            variant="ghost"
            onClick={() => setCollapsed(false)}
            className="patient-edit-btn"
          >
            {t("patient.edit")}
          </Button>
        </div>
        <p className="patient-text-excerpt">{workspace.description}</p>
      </Panel>
    );
  }

  const canCollapse = findings.length > 0 && !introductory;

  return (
    <Panel
      className={`clinical-input-panel ${introductory ? "clinical-input-panel--composer" : "clinical-input-panel--editing"}`}
      aria-label={t("input.label")}
    >
      <form
        className="composer-card"
        onSubmit={(event) => {
          event.preventDefault();
          analyze();
        }}
      >
        <label
          htmlFor="clinical-description-textarea"
          className={introductory ? "sr-only" : "control-label input-label"}
        >
          {t("input.label")}
        </label>

        {showSamples && (
          <div id="sample-picker" className="sample-picker">
            <label htmlFor="clinical-scenario-select" className="control-label">
              {t("input.scenario")}
            </label>
            <div className="scenario-row">
              <select
                id="clinical-scenario-select"
                value={scenario}
                onChange={(event) => setScenario(event.target.value as Scenario)}
                disabled={busy}
                className="scenario-select"
              >
                {(
                  [
                    "skeletal",
                    "prenatal",
                    "ambiguous",
                    "empty",
                    "insufficient",
                  ] as const
                ).map((value) => (
                  <option key={value} value={value}>
                    {t(`scenario.${value}`)}
                  </option>
                ))}
              </select>
              <Button variant="secondary" onClick={loadSample} disabled={busy}>
                {t("input.sample")}
              </Button>
            </div>
          </div>
        )}

        <textarea
          ref={input}
          id="clinical-description-textarea"
          value={workspace.description}
          onChange={(event) =>
            dispatch(workspaceActions.descriptionChanged(event.target.value))
          }
          placeholder={t("input.placeholder")}
          rows={introductory ? 5 : 6}
          disabled={busy}
          className="description-textarea"
        />

        <div className="composer-bar" aria-live="polite">
          <Button
            variant="ghost"
            onClick={() => setShowSamples((value) => !value)}
            disabled={busy}
            aria-expanded={showSamples}
            aria-controls="sample-picker"
            className="composer-bar__examples"
          >
            {t("input.examples")}
          </Button>
          <div className="composer-bar__actions">
            {canCollapse && (
              <Button variant="ghost" onClick={() => setCollapsed(true)}>
                {t("patient.collapse")}
              </Button>
            )}
            <Button
              type="submit"
              variant="primary"
              disabled={!workspace.description.trim() || busy}
              className="analyze-btn"
            >
              {busy && <span className="button-spinner" aria-hidden="true" />}
              {t(
                busy
                  ? `status.${analysis.status}`
                  : run
                    ? "input.reanalyze"
                    : "input.analyze",
              )}
            </Button>
          </div>
        </div>
      </form>
    </Panel>
  );
}
