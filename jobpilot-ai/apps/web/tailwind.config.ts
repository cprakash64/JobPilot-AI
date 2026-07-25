import type { Config } from "tailwindcss";

/*
 * The palette names below map onto the semantic CSS variables defined in
 * app/globals.css. That indirection is what makes the whole application
 * theme-aware: every existing `bg-panel`, `border-line`, `text-ink` or
 * `bg-white` in a component resolves to a token that already has a light and a
 * dark value, so surfaces follow the OS preference without per-component
 * `dark:` overrides.
 *
 * `white` is remapped deliberately: throughout this codebase it means "card
 * surface", not literal white, and leaving it hard-coded would leave bright
 * panels floating in dark mode. Use `text-accent-foreground` when you genuinely
 * need a colour that contrasts against the brand green.
 *
 * darkMode uses Tailwind's "media" strategy, so `dark:` variants follow
 * `prefers-color-scheme`. They are used sparingly — only where a token cannot
 * express the change, e.g. the fit-score hues in lib/fitScore.ts.
 */
const config: Config = {
  darkMode: "media",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Semantic tokens
        background: "var(--background)",
        surface: "var(--surface)",
        "surface-raised": "var(--surface-raised)",
        "surface-muted": "var(--surface-muted)",
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-muted": "var(--text-muted)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        accent: "var(--accent)",
        "accent-hover": "var(--accent-hover)",
        "accent-foreground": "var(--accent-foreground)",
        success: "var(--success)",
        "success-surface": "var(--success-surface)",
        "success-border": "var(--success-border)",
        warning: "var(--warning)",
        "warning-surface": "var(--warning-surface)",
        "warning-border": "var(--warning-border)",
        danger: "var(--danger)",
        "danger-hover": "var(--danger-hover)",
        "danger-surface": "var(--danger-surface)",
        "danger-border": "var(--danger-border)",
        overlay: "var(--overlay)",

        // Legacy names, kept so existing markup keeps working — now token-backed.
        ink: "var(--text-primary)",
        panel: "var(--surface-muted)",
        line: "var(--border)",
        pine: "var(--accent)",
        coral: "var(--danger)",
        sky: "var(--focus-ring)",

        // "white" means "card surface" in this codebase, not literal white.
        white: "var(--surface)"
      },
      boxShadow: {
        card: "var(--shadow)"
      }
    }
  },
  plugins: []
};

export default config;
