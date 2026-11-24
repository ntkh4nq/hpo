import { cloneElement, useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Supplemental help, available on hover and keyboard focus, outside clipping panels. */
export function Tooltip({ children, content }: {
  children: ReactElement<{ 'aria-describedby'?: string; title?: string }>;
  content: ReactNode;
}) {
  const id = useId();
  const anchor = useRef<HTMLDivElement>(null);
  const bubble = useRef<HTMLDivElement>(null);
  const delay = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  const cancelDelay = () => { if (delay.current) clearTimeout(delay.current); };
  const show = () => { cancelDelay(); setOpen(true); };
  const hide = () => { cancelDelay(); delay.current = setTimeout(() => setOpen(false), 100); };

  useEffect(() => () => cancelDelay(), []);
  useLayoutEffect(() => {
    if (!open) return;
    const reposition = () => {
      if (!anchor.current || !bubble.current) return;
      const target = anchor.current.getBoundingClientRect();
      const hint = bubble.current.getBoundingClientRect();
      const left = Math.max(12, Math.min(target.left + (target.width - hint.width) / 2, window.innerWidth - hint.width - 12));
      const below = target.bottom + 10;
      const top = below + hint.height <= window.innerHeight - 12 ? below : Math.max(12, target.top - hint.height - 10);
      setPosition({ left, top });
    };
    reposition();
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        cancelDelay();
        setOpen(false);
      }
    };
    document.addEventListener('keydown', dismiss, true);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
      document.removeEventListener('keydown', dismiss, true);
    };
  }, [open, content]);

  return <>
    <div
      ref={anchor}
      className="tooltip-anchor"
      onMouseEnter={() => { cancelDelay(); delay.current = setTimeout(() => setOpen(true), 250); }}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onClick={event => {
        // An activated action has served its purpose; keep help from covering its result.
        if (event.target instanceof Element && event.target.closest('button')) {
          cancelDelay();
          setOpen(false);
        }
      }}
    >
      {cloneElement(children, {
        title: undefined,
        'aria-describedby': [children.props['aria-describedby'], open ? id : undefined].filter(Boolean).join(' ') || undefined,
      })}
    </div>
    {open && createPortal(
      <div ref={bubble} id={id} role="tooltip" className="tooltip-bubble" style={position} onMouseEnter={show} onMouseLeave={hide}>{content}</div>,
      document.body,
    )}
  </>;
}
