import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import vi from './locales/vi.json';
let language = 'vi';
try { language = localStorage.getItem('phenotype-language') === 'en' ? 'en' : 'vi'; } catch { /* Storage may be disabled. */ }
void i18n.use(initReactI18next).init({ resources: {en:{translation:en},vi:{translation:vi}}, lng:language, fallbackLng:'en', keySeparator:false, interpolation:{escapeValue:false} });
document.documentElement.lang = language;
i18n.on('languageChanged', lng => { document.documentElement.lang = lng; try { localStorage.setItem('phenotype-language', lng); } catch { /* In-memory language still works. */ } });
export { i18n };
