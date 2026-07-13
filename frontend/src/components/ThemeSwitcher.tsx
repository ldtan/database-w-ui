import { useRef } from "react";
import { Button } from "primereact/button";
import { OverlayPanel } from "primereact/overlaypanel";

import { THEMES, useTheme } from "../lib/theme";

/**
 * Two header buttons: light/dark, and the palette picker.
 *
 * The toggle is a button rather than an item in a menu because a control you have
 * to open a panel to reach is one people stop using — it stays visible even on a
 * phone, where the rest of the navigation collapses into the burger.
 */
export function ThemeSwitcher() {
  const { theme, mode, setTheme, toggleMode } = useTheme();
  const panel = useRef<OverlayPanel>(null);

  return (
    <div className="flex items-center gap-1">
      <Button
        icon={mode === "dark" ? "pi pi-sun" : "pi pi-moon"}
        text
        rounded
        aria-label={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        onClick={toggleMode}
      />
      <Button
        icon="pi pi-palette"
        text
        rounded
        aria-label="Choose theme"
        onClick={(e) => panel.current?.toggle(e)}
      />
      <OverlayPanel ref={panel}>
        <div className="flex flex-col gap-1" style={{ minWidth: "16rem" }}>
          {THEMES.map((t) => {
            const active = t.id === theme;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  setTheme(t.id);
                  panel.current?.hide();
                }}
                className="flex items-center gap-3 rounded px-2 py-2 text-left"
                style={{
                  border: active
                    ? "2px solid var(--brand)"
                    : "1px solid var(--border)",
                  background: active ? "var(--brand-tint)" : "transparent",
                  color: "var(--text)",
                  cursor: "pointer",
                }}
              >
                <span className="flex gap-1">
                  {t.swatch.map((c) => (
                    <span
                      key={c}
                      style={{
                        width: 16,
                        height: 16,
                        borderRadius: 4,
                        background: c,
                        border: "1px solid rgba(0,0,0,0.15)",
                      }}
                    />
                  ))}
                </span>
                <span className="flex flex-col">
                  <span className="font-medium">{t.label}</span>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    {t.description}
                  </span>
                </span>
                {active && <i className="pi pi-check ml-auto" style={{ color: "var(--brand)" }} />}
              </button>
            );
          })}
        </div>
      </OverlayPanel>
    </div>
  );
}
