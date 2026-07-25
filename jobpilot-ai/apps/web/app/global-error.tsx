"use client";

/**
 * Root error boundary. Replaces the entire document, including <html>/<body>,
 * so it cannot rely on the app's stylesheet being applied — the theme tokens
 * are inlined here instead.
 *
 * `color-scheme: light dark` plus a media-query background is what stops this
 * page rendering as a white sheet for a user in dark mode.
 */

export default function GlobalError({
  error,
  reset
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <head>
        <meta name="color-scheme" content="light dark" />
        <style
          // Inlined: at this point the app's CSS may not have loaded.
          dangerouslySetInnerHTML={{
            __html: `
              :root { color-scheme: light dark; --bg:#fbfbf8; --surface:#fff; --fg:#17211b; --muted:#5d675f; --line:#d8ddd2; --accent:#1f5e45; --accent-fg:#fff; }
              @media (prefers-color-scheme: dark) {
                :root { --bg:#14171a; --surface:#1b1f22; --fg:#eef1ed; --muted:#98a29b; --line:#333a3d; --accent:#4c9e7a; --accent-fg:#0d1310; }
              }
              html,body { margin:0; background:var(--bg); color:var(--fg);
                font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
              .wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }
              .card { max-width:28rem; width:100%; background:var(--surface); border:1px solid var(--line);
                border-radius:12px; padding:24px; }
              h1 { font-size:1.25rem; margin:0 0 8px; }
              p { color:var(--muted); font-size:0.875rem; line-height:1.5; margin:0 0 20px; }
              button { height:40px; padding:0 16px; border:0; border-radius:6px; cursor:pointer;
                background:var(--accent); color:var(--accent-fg); font-size:0.875rem; font-weight:500; }
              button:focus-visible { outline:3px solid #2f6f9f; outline-offset:2px; }
              code { color:var(--muted); font-size:0.75rem; }
            `
          }}
        />
      </head>
      <body>
        <div className="wrap">
          <div className="card" role="alert">
            <h1>JobPilot AI could not load</h1>
            <p>An unexpected error stopped the application. Your saved data is unaffected.</p>
            <button type="button" onClick={reset}>
              Reload
            </button>
            {error.digest && (
              <p style={{ marginTop: 16, marginBottom: 0 }}>
                Reference: <code>{error.digest}</code>
              </p>
            )}
          </div>
        </div>
      </body>
    </html>
  );
}
