import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode, Ref } from 'react';
export function Button({variant = 'primary', className = '', ...props}: ButtonHTMLAttributes<HTMLButtonElement> & {variant?: 'primary'|'secondary'|'ghost'; ref?: Ref<HTMLButtonElement>}) { return <button type="button" className={`button button--${variant} ${className}`} {...props}/>; }
export function Panel({children, className = '', ...props}: HTMLAttributes<HTMLElement>) { return <section className={`panel ${className}`} {...props}>{children}</section>; }
export function Badge({children, tone = 'neutral'}: {children:ReactNode; tone?:'neutral'|'teal'|'amber'}) { return <span className={`badge badge--${tone}`}>{children}</span>; }
export function Feedback({children, error=false}: {children:ReactNode; error?:boolean}) { return <div className={`feedback ${error?'feedback--error':''}`} role={error?'alert':'status'}>{children}</div>; }
export function Icon({name, size=20}: {name:'check'|'close'|'plus'|'chevron'|'user'|'history'|'edit'|'trash'|'sun'|'moon'; size?:number}) {
 const paths: Record<string, ReactNode> = {
 edit:<><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15l-1 6 1-1Z"/></>,
 trash:<><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></>,
 user:<><circle cx="12" cy="8" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/></>,
 history:<><path d="M3 11a9 9 0 1 1 2.6 7.4M3 4v7h7"/><path d="M12 7v5l3 2"/></>,
 check:<path d="m5 12 4 4L19 6"/>,close:<path d="m6 6 12 12M6 18 18 6"/>,plus:<path d="M12 5v14M5 12h14"/>,chevron:<path d="m9 5 7 7-7 7"/>,
 sun:<><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></>,
 moon:<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
 }; return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

/** Text-only empty state. */
export function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="empty-state">
      <h3 className="empty-state__title">{title}</h3>
      <p className="empty-state__hint">{hint}</p>
    </div>
  );
}
