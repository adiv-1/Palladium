import {useCallback, useEffect, useState} from 'react';

export type Theme = 'light' | 'dark';
const KEY = 'safesc-theme';

const read = (): Theme | null => {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'dark' || v === 'light' ? v : null;
  } catch {
    return null;
  }
};

export const currentTheme = (): Theme => (document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');

// Saved choice wins; otherwise follow the system. The embedded reviewer-home frame starts light
// because the site around it is light.
export function initTheme() {
  const embedded = new URLSearchParams(location.search).get('embedded') === '1';
  const system = !embedded && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = read() ?? system;
}

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {localStorage.setItem(KEY, theme);} catch { /* storage blocked */ }
  window.dispatchEvent(new Event('safesc-theme'));
}

// [theme, toggle]. Re-renders when the theme changes here or in another tab.
export function useTheme(): [Theme, () => void] {
  const [theme, set] = useState<Theme>(currentTheme());
  useEffect(() => {
    const sync = () => {
      const saved = read();
      if (saved && saved !== currentTheme()) document.documentElement.dataset.theme = saved;
      set(currentTheme());
    };
    window.addEventListener('safesc-theme', sync);
    window.addEventListener('storage', sync);
    // Also react when anything else flips data-theme (devtools, tests, another script).
    const watcher = new MutationObserver(() => set(currentTheme()));
    watcher.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
    return () => {window.removeEventListener('safesc-theme', sync); window.removeEventListener('storage', sync); watcher.disconnect();};
  }, []);
  const toggle = useCallback(() => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'), []);
  return [theme, toggle];
}

// Colours for deck.gl text, which is drawn on the map and cannot use CSS variables.
export const isDark = () => currentTheme() === 'dark';
export const outlineRGBA = (): [number, number, number, number] => (isDark() ? [18, 20, 23, 255] : [238, 238, 240, 255]);
export const labelRGBA = (alpha = 220): [number, number, number, number] => (isDark() ? [220, 224, 230, alpha] : [38, 42, 50, alpha]);
