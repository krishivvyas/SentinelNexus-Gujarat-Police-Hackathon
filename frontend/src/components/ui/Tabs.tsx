import React from 'react';
import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

interface TabItem {
  id: string;
  label: string;
  count?: number;
  icon?: React.ReactNode;
}

interface TabsProps {
  tabs: TabItem[];
  activeId: string;
  onChange: (id: string) => void;
  className?: string;
  size?: 'sm' | 'md';
}

export function Tabs({
  tabs,
  activeId,
  onChange,
  className,
  size = 'md',
}: TabsProps) {
  return (
    <div
      className={twMerge(
        clsx(
          'inline-flex items-center p-1 rounded-xl bg-slate-100/90 border border-slate-200/80 shadow-[inset_0_1px_2px_0_rgba(0,0,0,0.06)] select-none',
          className
        )
      )}
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeId;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-lg font-semibold transition-all cursor-pointer',
              size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3.5 py-1.5 text-xs',
              isActive
                ? 'bg-white text-slate-900 shadow-sm border border-slate-200/80'
                : 'text-slate-600 hover:text-slate-900 border border-transparent'
            )}
          >
            {tab.icon && <span className="flex-none">{tab.icon}</span>}
            <span>{tab.label}</span>
            {tab.count !== undefined && (
              <span
                className={clsx(
                  'px-1.5 py-0.2 rounded-full text-[10px] font-mono',
                  isActive
                    ? 'bg-slate-100 text-slate-700'
                    : 'bg-slate-200 text-slate-600'
                )}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
