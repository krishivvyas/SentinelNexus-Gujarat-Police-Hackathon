import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  hoverable?: boolean;
  spec?: string;
  header?: React.ReactNode;
  action?: React.ReactNode;
}

export function Card({
  hoverable = false,
  spec,
  header,
  action,
  className,
  children,
  ...props
}: CardProps) {
  return (
    <div
      className={twMerge(
        clsx(
          'relative bg-white rounded-xl border border-slate-200/90 p-5 shadow-[0_2px_8px_-2px_rgba(0,0,0,0.04)] transition-all',
          hoverable && 'hover:-translate-y-0.5 hover:shadow-[0_8px_24px_-4px_rgba(0,0,0,0.08)] cursor-pointer',
          className
        )
      )}
      {...props}
    >
      {(header || spec || action) && (
        <div className="flex items-center justify-between gap-3 mb-4 pb-3 border-b border-slate-100">
          <div className="flex items-center gap-2.5 min-w-0">
            {header && (
              <h3 className="font-bold text-slate-900 text-base tracking-tight truncate">
                {header}
              </h3>
            )}
            {spec && (
              <span className="px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-blue-50 text-blue-800 border border-blue-200/80 flex-none">
                {spec}
              </span>
            )}
          </div>
          {action && <div className="flex-none">{action}</div>}
        </div>
      )}
      {children}
    </div>
  );
}
