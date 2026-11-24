import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAppDispatch, useAppSelector } from "../redux";
import { workspaceActions } from "../redux/slices";
import { selectWorkspace } from "../redux/selectors";
import { EditableTitle, Tooltip } from "../shared/ui";
import { SidebarGlyph } from "../features/navigation";

export function WorkspaceHeader({
  showMenuButton,
  onOpenSidebar,
  stepper,
}: {
  showMenuButton: boolean;
  onOpenSidebar: () => void;
  stepper?: ReactNode;
}) {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const workspace = useAppSelector(selectWorkspace);

  return (
    <header className="app-header">
      <div className="header-left">
        {showMenuButton && (
          <Tooltip content={t("help.expand")}>
            <button
              id="header-menu-trigger"
              type="button"
              className="icon-button"
              onClick={onOpenSidebar}
              aria-label={t("nav.expand")}
            >
              <SidebarGlyph />
            </button>
          </Tooltip>
        )}
        <EditableTitle
          key={workspace.caseKey}
          value={workspace.title}
          fallback={t("case.untitled")}
          onSave={(title) => dispatch(workspaceActions.titleChanged(title))}
        />
      </div>
      <div className="header-center">{stepper}</div>
    </header>
  );
}
