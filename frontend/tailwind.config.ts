import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        ultra: {
          blue: '#2563EB',
          'blue-dark': '#1D4ED8',
          'blue-light': '#3B82F6',
          canvas: '#FAFAFA',
          subtle: '#F6F6F8',
          surface: '#FFFFFF',
          ink: '#0F172A',
          'ink-dim': '#475569',
          'ink-muted': '#64748B',
          'ink-faint': '#94A3B8',
          line: 'rgb(226 232 240 / 0.9)',
          'line-strong': '#CBD5E1',
          ok: '#16A34A',
          'ok-soft': '#DCFCE7',
          warn: '#D97706',
          'warn-soft': '#FEF3C7',
          crit: '#DC2626',
          'crit-soft': '#FEE2E2',
        },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'JetBrains Mono', 'Menlo', 'monospace'],
      },
      boxShadow: {
        'tactile-btn': 'inset 0 1px 0 0 rgba(255,255,255,0.35), 0 1px 2px 0 rgba(0,0,0,0.06)',
        'tactile-sec': 'inset 0 1px 0 0 rgba(255,255,255,1), 0 1px 2px 0 rgba(0,0,0,0.04)',
        'recessed-well': 'inset 0 1px 2px 0 rgba(0,0,0,0.06)',
        'bento-card': '0 2px 8px -2px rgba(0,0,0,0.04)',
        'bento-hover': '0 8px 24px -4px rgba(0,0,0,0.08)',
        'modal-pop': '0 20px 40px -12px rgba(0,0,0,0.18)',
      },
    },
  },
  plugins: [],
};

export default config;
