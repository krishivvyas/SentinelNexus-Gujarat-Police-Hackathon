/* Theme: dark (default), light, or follow the operating system.
 *
 * Three things about this are deliberate and worth not undoing.
 *
 * **Dark is the default, and "auto" is opt-in.** The obvious implementation is
 * a bare `prefers-color-scheme` media query, which would hand every operator
 * whatever their OS happens to say. That is the wrong default here. This
 * interface's home is a wall in a room kept dim so camera feeds stay readable,
 * and an OS in light mode is not evidence about that room -- a workstation
 * shipped with the vendor default tells you nothing about where it ended up. So
 * an operator who has never touched the control gets dark, on the wall and on a
 * laptop alike, and the OS is consulted only by someone who asked for it.
 *
 * **The resolution happens in script, not in CSS.** `auto` is turned into a
 * concrete `light` or `dark` here and written to `<html data-theme>`, so
 * tokens.css holds exactly one light palette instead of two copies of it -- one
 * for `[data-theme="light"]` and one inside a media query. Two copies of a
 * palette drift, and the drift shows up as a single wrong border six months on.
 *
 * **The stored value is the preference, not the result.** Storing the resolved
 * theme would freeze an operator on "auto" into whichever mode they happened to
 * be in when they chose it, and it would never follow the OS again.
 *
 * The no-flash inline snippet in the page <head> duplicates `applyTheme` on
 * purpose: a module script cannot run before first paint, and a light-mode
 * operator should not eat a black flash on every navigation. If the token
 * names here change, change them there too -- `SYNC_NOTE` in wall.html.
 */

const STORE_KEY = 'sentinel.theme';

export const THEMES = [
  { key: 'dark',  label: 'Dark',  hint: 'For the wall and for dim rooms. The default.' },
  { key: 'light', label: 'Light', hint: 'For a lit office, a projector or a screenshot in a report.' },
  { key: 'auto',  label: 'Auto',  hint: 'Follow this machine\u2019s operating system setting.' },
];

const VALID = new Set(THEMES.map((t) => t.key));
const prefersLight = window.matchMedia('(prefers-color-scheme: light)');

/* Fired after the resolved theme changes, whether from the operator picking one
 * or from the OS flipping underneath an "auto" session. Anything that has
 * baked a colour into a canvas or an inline style listens for this. */
export const themeEvents = new EventTarget();

function stored() {
  try {
    const value = localStorage.getItem(STORE_KEY);
    return VALID.has(value) ? value : 'dark';
  } catch {
    // Private mode. Dark rather than auto: the default has to be the same
    // value a fresh browser gets, or the wall changes appearance depending on
    // whether storage happened to be available.
    return 'dark';
  }
}

/** 'auto' -> what the OS actually says. 'dark'/'light' -> themselves. */
function resolve(preference) {
  if (preference === 'auto') return prefersLight.matches ? 'light' : 'dark';
  return preference;
}

function applyTheme(resolved) {
  document.documentElement.dataset.theme = resolved;
}

export const theme = {
  /** What the operator chose: 'dark' | 'light' | 'auto'. */
  get preference() { return stored(); },

  /** What is actually on screen: 'dark' | 'light'. */
  get resolved() { return resolve(stored()); },

  set(preference) {
    if (!VALID.has(preference)) return;
    try { localStorage.setItem(STORE_KEY, preference); }
    catch { /* private mode: the choice holds for this page and no longer */ }
    const resolved = resolve(preference);
    applyTheme(resolved);
    themeEvents.dispatchEvent(new CustomEvent('change', {
      detail: { preference, resolved },
    }));
    return resolved;
  },
};

// Apply immediately on import, so a page that forgot the inline no-flash
// snippet is merely late rather than wrong.
applyTheme(resolve(stored()));

// Only meaningful while the preference is 'auto'; harmless otherwise, and
// cheaper than adding and removing the listener as the preference changes.
prefersLight.addEventListener('change', () => {
  if (stored() !== 'auto') return;
  const resolved = resolve('auto');
  applyTheme(resolved);
  themeEvents.dispatchEvent(new CustomEvent('change', {
    detail: { preference: 'auto', resolved },
  }));
});
