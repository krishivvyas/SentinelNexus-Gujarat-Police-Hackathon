import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  icon?: React.ReactNode;
  label?: string;
  error?: string;
}

export function Input({
  icon,
  label,
  error,
  className,
  ...props
}: InputProps) {
  return (
    <div className="w-full">
      {label && (
        <label className="block text-xs font-mono font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
          {label}
        </label>
      )}
      <div className="relative flex items-center">
        {icon && (
          <div className="absolute left-3 text-slate-400 pointer-events-none flex items-center justify-center">
            {icon}
          </div>
        )}
        <input
          className={twMerge(
            clsx(
              'w-full py-2 text-sm rounded-lg bg-slate-50/80 border border-slate-200/90 text-slate-900 placeholder:text-slate-400',
              'shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.05)] transition-all',
              'focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB]/20 focus:border-[#2563EB]',
              icon ? 'pl-9 pr-3.5' : 'px-3.5',
              error && 'border-red-500 focus:border-red-500 focus:ring-red-500/20',
              className
            )
          )}
          {...props}
        />
      </div>
      {error && <p className="mt-1 text-xs text-red-600 font-medium">{error}</p>}
    </div>
  );
}

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

export function Textarea({
  label,
  error,
  className,
  ...props
}: TextareaProps) {
  return (
    <div className="w-full">
      {label && (
        <label className="block text-xs font-mono font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
          {label}
        </label>
      )}
      <textarea
        className={twMerge(
          clsx(
            'w-full p-3 text-sm rounded-lg bg-slate-50/80 border border-slate-200/90 text-slate-900 placeholder:text-slate-400 font-mono',
            'shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.05)] transition-all',
            'focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB]/20 focus:border-[#2563EB]',
            error && 'border-red-500 focus:border-red-500 focus:ring-red-500/20',
            className
          )
        )}
        {...props}
      />
      {error && <p className="mt-1 text-xs text-red-600 font-medium">{error}</p>}
    </div>
  );
}
