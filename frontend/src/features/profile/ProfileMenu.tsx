import { useEffect, useId, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useTranslation } from "react-i18next";
import { Icon, Tooltip } from "../../shared/ui";

export function ProfileMenu({ compact = false }: { compact?: boolean }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage?.startsWith("vi") ? "vi" : "en";

  const [theme, setTheme] = useState<"light" | "dark">(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem("app-theme"); } catch { /* storage may be blocked */ }
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("app-theme", theme); } catch { /* theme still applies in memory */ }
  }, [theme]);

  // Cross-fade between themes: View Transitions where supported, otherwise a short colour transition.
  const toggleTheme = (newTheme: "light" | "dark") => {
    if (newTheme === theme) return;
    const root = document.documentElement;
    const apply = () => {
      root.setAttribute("data-theme", newTheme);
      flushSync(() => setTheme(newTheme));
    };
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { apply(); return; }
    if (typeof document.startViewTransition === "function") { document.startViewTransition(apply); return; }
    root.classList.add("theme-transition");
    apply();
    window.setTimeout(() => root.classList.remove("theme-transition"), 260);
  };

  const menuId = `profile-menu-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const trigger = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const menu = popover.current;
    const button = trigger.current;
    if (!menu || !button) return;
    // Opens upward from the sidebar footer, aligned to the trigger's left edge.
    const position = () => {
      const rect = button.getBoundingClientRect();
      const width = Math.min(260, window.innerWidth - 24);
      menu.style.width = `${width}px`;
      menu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
      menu.style.top = 'auto';
      menu.style.bottom = `${Math.max(12, window.innerHeight - rect.top + 8)}px`;
      menu.style.maxHeight = `${Math.max(120, rect.top - 20)}px`;
    };
    const beforeToggle = (event: Event) => {
      if ((event as ToggleEvent).newState === 'open') position();
    };
    menu.addEventListener('beforetoggle', beforeToggle);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      menu.removeEventListener('beforetoggle', beforeToggle);
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, []);

  return (
    <div className="profile-control">
      <Tooltip content={t("help.profile")}>
        <button
          type="button"
          ref={trigger}
          className={`profile-button ${compact ? "profile-button--compact" : ""}`}
          popoverTarget={menuId}
          aria-label={t("profile.title")}
          aria-haspopup="dialog"
        >
          <span className="profile-button__avatar" aria-hidden="true"><Icon name="user" size={16} /></span>
          {!compact && <>
            <span className="profile-button__text">
              <span className="profile-button__name">{t("profile.guest")}</span>
              <span className="profile-button__role">{t("profile.clinicianRole")}</span>
            </span>
            <span className="profile-button__lang" aria-hidden="true">{locale.toUpperCase()}</span>
          </>}
        </button>
      </Tooltip>
      <div
        ref={popover} id={menuId}
        popover="auto"
        className="profile-popover"
        role="dialog"
        aria-labelledby={`${menuId}-title`}
      >
        {/* Clinician Profile Header */}
        <div className="profile-header">
          <div className="profile-avatar-wrap">
            <span className="profile-avatar" aria-hidden="true">
              <Icon name="user" size={22} />
            </span>
          </div>
          <div className="profile-header-info">
            <div className="profile-name-row">
              <h2 id={`${menuId}-title`}>{t("profile.guest")}</h2>
            </div>
            <p className="profile-subtitle">{t("profile.clinicianRole")}</p>
          </div>
        </div>

        <div className="profile-divider" />

        {/* Preferences / Settings */}
        <div className="profile-settings-section">
          <div className="profile-setting-row">
            <span id={`${menuId}-language`} className="profile-setting-label">
              {t("common.language")}
            </span>
            <div
              className="language-switch"
              data-language={locale}
              role="group"
              aria-labelledby={`${menuId}-language`}
            >
              {(["vi", "en"] as const).map((language) => (
                <Tooltip
                  key={language}
                  content={t(`help.language.${language}`)}
                >
                  <button
                    type="button"
                    className={`lang-btn ${locale === language ? "lang-btn--active" : ""}`}
                    aria-pressed={locale === language}
                    onClick={() => {
                      void i18n.changeLanguage(language);
                    }}
                  >
                    {language.toUpperCase()}
                  </button>
                </Tooltip>
              ))}
            </div>
          </div>

          <div className="profile-setting-row">
            <span id={`${menuId}-theme`} className="profile-setting-label">
              {t("profile.theme")}
            </span>
            <div
              className="theme-switch"
              role="group"
              aria-labelledby={`${menuId}-theme`}
            >
              {(["light", "dark"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`theme-btn ${theme === value ? "theme-btn--active" : ""}`}
                  aria-pressed={theme === value}
                  onClick={() => toggleTheme(value)}
                  title={t(`theme.${value}`)}
                >
                  <Icon name={value === "light" ? "sun" : "moon"} size={14} />
                  <span>{t(`theme.${value}`)}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
