import type { useT } from "./i18n";

type T = ReturnType<typeof useT>;

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Today, 9:37 AM", "Yesterday, 6:10 PM" or "14 Oct 2026, 9:37 AM", without seconds. */
export function formatWhen(t: T, iso: string): string {
  const date = new Date(iso);
  const time = date.toLocaleTimeString(t.locale, { hour: "numeric", minute: "2-digit" });
  const now = new Date();
  if (sameDay(date, now)) return t("today", { time });
  if (sameDay(date, new Date(now.getTime() - 86_400_000))) return t("yesterday", { time });
  return date.toLocaleString(t.locale, { dateStyle: "medium", timeStyle: "short" });
}
