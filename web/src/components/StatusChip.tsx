import { useT } from "../i18n";
import type { ObservationStatus } from "../types";

export function StatusChip({ status }: { status: ObservationStatus }) {
  const t = useT();
  return <span className={`chip ${status}`}>{t.dynamic(`status_${status}`, "status_submitted")}</span>;
}
