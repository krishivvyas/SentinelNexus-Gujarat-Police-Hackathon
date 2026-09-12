import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface KbdProps extends React.HTMLAttributes<HTMLElement> {
  keys?: string[];
}

export function Kbd({ keys, className, children, ...props }: KbdProps) {
  return (
    <kbd
      className={twMerge(
        clsx(
          'inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono font-semibold text-slate-700 bg-slate-100 border border-slate-300 rounded shadow-[inset_0_-1px_0_0_rgba(0,0,0,0.1)] select-none',
          className
        )
      )}
      {...props}
    >
      {keys ? keys.join('') : children}
    </kbd>
  );
}
