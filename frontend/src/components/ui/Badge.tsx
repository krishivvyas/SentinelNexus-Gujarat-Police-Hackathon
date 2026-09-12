import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'ok' | 'warn' | 'crit' | 'blue' | 'slate' | 'ai';
  dot?: boolean;
  pulse?: boolean;
}

export function Badge({
  variant = 'slate',
  dot = true,
  pulse = false,
  className,
  children,
  ...props
}: BadgeProps) {
  const styles = {
    ok: 'bg-emerald-50 text-emerald-800 border-emerald-200/90',
    warn: 'bg-amber-50 text-amber-800 border-amber-200/90',
    crit: 'bg-rose-50 text-rose-800 border-rose-200/90',
    blue: 'bg-blue-50 text-blue-800 border-blue-200/90',
    slate: 'bg-slate-100 text-slate-700 border-slate-200/90',
    ai: 'bg-purple-50 text-purple-800 border-purple-200/90',
  };

  const dotColors = {
    ok: 'bg-emerald-500',
    warn: 'bg-amber-500',
    crit: 'bg-rose-500',
    blue: 'bg-[#2563EB]',
    slate: 'bg-slate-500',
    ai: 'bg-purple-500',
  };

  return (
    <span
      className={twMerge(
        clsx(
          'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-xs font-mono font-medium border transition-colors select-none',
          styles[variant],
          className
        )
      )}
      {...props}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5">
          {pulse && (
            <span
              className={clsx(
                'animate-ping absolute inline-flex h-full w-full rounded-full opacity-75',
                dotColors[variant]
              )}
            />
          )}
          <span className={clsx('relative inline-flex rounded-full h-1.5 w-1.5', dotColors[variant])} />
        </span>
      )}
      {children}
    </span>
  );
}
