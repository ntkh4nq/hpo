/** Same rule as the backend (api/cases.py: TitleIn; agent/config.toml case_title_chars = 60). */
export const CASE_TITLE_CHARS = 60;

export function normalizeCaseTitle(title: string) {
  return title.trim().replace(/\s+/g, ' ').slice(0, CASE_TITLE_CHARS);
}
