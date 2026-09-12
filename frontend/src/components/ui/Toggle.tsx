import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  description?: string;
  disabled?: boolean;
  className?: string;
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled = false,
  className,
}: ToggleProps) {
  return (
    <label
      className={twMerge(
        clsx(
          'inline-flex items-center gap-3 cursor-pointer select-none',
          disabled && 'opacity-50 pointer-events-none',
          className
        )
      )}
    >
      <div
        onClick={() => !disabled && onChange(!checked)}
        className={clsx(
          'relative w-11 h-6 rounded-full border transition-colors duration-200',
          checked
            ? 'bg-[#2563EB] border-[#1D4ED8] shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.15)]'
            : 'bg-slate-200 border-slate-300 shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.1)]'
        )}
      >
        <div
          className={clsx(
            'absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full border border-slate-200/80 shadow-sm transition-transform duration-200',
            checked ? 'translate-x-5' : 'translate-x-0'
          )}
        />
      </div>
      {(label || description) && (
        <div className="flex flex-col">
          {label && (
            <span className="text-xs font-mono font-medium text-slate-800">
              {label}
            </span>
          )}
          {description && (
            <span className="text-[11px] text-slate-500">
              {description}
            </span>
          )}
        </div>
      )}
    </label>
  );
}
