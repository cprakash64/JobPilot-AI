/**
 * Content-script instance ownership.
 *
 * Reloading an unpacked MV3 extension invalidates the old script's
 * `chrome.runtime` connection, but the page and its isolated-world globals can
 * stay alive.  A newly injected bundle must therefore be allowed to supersede
 * the old one; a boolean "already loaded" guard permanently traps the page on
 * the orphaned instance.
 */

export const CONTENT_INSTANCE_KEY = "__jobpilotContentInstance";

export function makeContentInstanceId(buildId: string): string {
  return `${buildId}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
}

export function claimContentInstance(
  target: Record<string, unknown>,
  instanceId: string
): () => boolean {
  target[CONTENT_INSTANCE_KEY] = instanceId;
  return () => target[CONTENT_INSTANCE_KEY] === instanceId;
}

