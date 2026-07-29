import { redirect } from "next/navigation";

/**
 * A job is not a separate screen: it opens inside the Jobs workspace, next to
 * the list it came from. This route stays as a permalink so existing links and
 * bookmarks keep working, and hands off to the workspace URL.
 */
export default async function JobDetailsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const numeric = Number.parseInt(id, 10);
  redirect(Number.isFinite(numeric) && numeric > 0 ? `/jobs?job=${numeric}` : "/jobs");
}
