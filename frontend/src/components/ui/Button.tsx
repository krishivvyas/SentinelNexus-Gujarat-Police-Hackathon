import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'danger' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  icon?: React.ReactNode;
}

export function Button({
  variant = 'primary',
  size = 'md',
  icon,
  className,
  children,
  ...props
}: ButtonProps) {
  const base =
    'relative inline-flex items-center justify-center gap-2 rounded-lg font-semibold text-sm transition-all select-none disabled:opacity-50 disabled:pointer-events-none active:scale-[0.98] cursor-pointer';

  const sizes = {
    sm: 'px-3 py-1.5 text-xs',
    md: 'px-4 py-2 text-sm',
    lg: 'px-5 py-2.5 text-base',
  };

  const variants = {
    primary:
      'bg-[#2563EB] text-white border border-slate-900/10 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.3),0_1px_2px_0_rgba(0,0,0,0.05)] hover:brightness-110',
    secondary:
      'bg-white text-slate-900 border border-slate-200 shadow-[inset_0_1px_0_0_rgba(255,255,255,1),0_1px_2px_0_rgba(0,0,0,0.03)] hover:bg-slate-50',
    outline:
      'bg-transparent text-slate-700 border border-slate-300 hover:bg-slate-100/70',
    danger:
      'bg-red-600 text-white border border-red-700 shadow-[inset_0_1px_0_0_rgba(255,255,255,0.25),0_1px_2px_0_rgba(0,0,0,0.05)] hover:bg-red-700',
    ghost:
      'bg-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900 border-none shadow-none',
  };

  return (
    <button
      className={twMerge(clsx(base, sizes[size], variants[variant], className))}
      {...props}
    >
      {icon && <span className="flex-none">{icon}</span>}
      {children}
    </button>
  );
}
