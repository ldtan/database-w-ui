import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ThemeId = "modern" | "nordic" | "brutalist" | "luxury";
export type ThemeMode = "light" | "dark";

export interface ThemeMeta {
  id: ThemeId;
  label: string;
  description: string;
  /** [primary, secondary, accent] preview swatches (light-mode values). */
  swatch: [string, string, string];
}

export const THEMES: ThemeMeta[] = [
  {
    id: "modern",
    label: "Modern Tech",
    description: "Clean, vibrant indigo & cyan",
    swatch: ["#4f46e5", "#06b6d4", "#8b5cf6"],
  },
  {
    id: "nordic",
    label: "Minimalist Nordic",
    description: "Warm, organic earth tones",
    swatch: ["#2d3a3a", "#786f68", "#d4a373"],
  },
  {
    id: "brutalist",
    label: "Neo-Brutalist",
    description: "Bold, high-contrast neon",
    swatch: ["#ff0055", "#00ffcc", "#ffcc00"],
  },
  {
    id: "luxury",
    label: "Elegant Luxury",
    description: "Deep emerald & warm gold",
    swatch: ["#064e3b", "#374151", "#d97706"],
  },
];

const THEME_KEY = "ui-theme";
const MODE_KEY = "ui-mode";
const DEFAULT_THEME: ThemeId = "modern";
const DEFAULT_MODE: ThemeMode = "light";

const THEME_IDS = new Set(THEMES.map((t) => t.id));

function readTheme(): ThemeId {
  const stored = localStorage.getItem(THEME_KEY);
  return stored && THEME_IDS.has(stored as ThemeId)
    ? (stored as ThemeId)
    : DEFAULT_THEME;
}

function readMode(): ThemeMode {
  const stored = localStorage.getItem(MODE_KEY);
  return stored === "dark" || stored === "light" ? stored : DEFAULT_MODE;
}

interface ThemeContextValue {
  theme: ThemeId;
  mode: ThemeMode;
  setTheme: (theme: ThemeId) => void;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
  /**
   * Bumped by `setTheme`/`setMode` — that is, by the user — and *not* by
   * `applyRemote`. It is how `ThemeSync` tells a choice someone just made from one
   * it pushed down from the account, which is the difference between saving a
   * preference and echoing it back and forth forever.
   */
  revision: number;
  /**
   * Apply the values stored against the account, without counting as a local
   * change. Writes localStorage like the setters do, so the pre-paint script in
   * `index.html` has them on the next load.
   */
  applyRemote: (theme: ThemeId, mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

/**
 * Palette + light/dark, remembered in localStorage.
 *
 * The provider holds no colors. It writes `data-theme` and `data-mode` onto
 * `<html>`, and `themes.css` keys every CSS variable off those two attributes — so
 * a new palette is a block of CSS and an entry in `THEMES`, with no component to
 * touch. Components must therefore reach for `var(--brand)`, `var(--text-muted)`
 * and friends rather than a Tailwind color, which would not follow the theme.
 *
 * `index.html` applies the saved values before first paint; without that the page
 * flashes the default theme on every load.
 *
 * localStorage is the device's cache, not the record: the account's copy on the
 * server is what follows the user between devices (see `ThemeSync`). It stays
 * because a value fetched over the network cannot arrive before first paint, and
 * the pre-paint script needs something synchronous to read.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(readTheme);
  const [mode, setModeState] = useState<ThemeMode>(readMode);
  const [revision, setRevision] = useState(0);

  // Reflect the current selection onto <html> so themes.css can key off it.
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.mode = mode;
  }, [theme, mode]);

  const setTheme = useCallback((next: ThemeId) => {
    localStorage.setItem(THEME_KEY, next);
    setThemeState(next);
    setRevision((r) => r + 1);
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    localStorage.setItem(MODE_KEY, next);
    setModeState(next);
    setRevision((r) => r + 1);
  }, []);

  const toggleMode = useCallback(() => {
    setMode(mode === "dark" ? "light" : "dark");
  }, [mode, setMode]);

  const applyRemote = useCallback((nextTheme: ThemeId, nextMode: ThemeMode) => {
    localStorage.setItem(THEME_KEY, nextTheme);
    localStorage.setItem(MODE_KEY, nextMode);
    setThemeState(nextTheme);
    setModeState(nextMode);
  }, []);

  const value = useMemo(
    () => ({ theme, mode, setTheme, setMode, toggleMode, revision, applyRemote }),
    [theme, mode, setTheme, setMode, toggleMode, revision, applyRemote],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}
