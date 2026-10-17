import { Bell, FileText, Sparkles } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "../api";
import { useT, type MessageKey } from "../i18n";
import { dashboardApi } from "./api";
import { taxonName } from "./Panels";
import type { AlertItem, AlertSettings, AnalystAnswer, Org, Report } from "./types";

// --- Alerts ------------------------------------------------------------------------------

export function AlertsPanel({ org, onUnread }: { org: Org; onUnread: (n: number) => void }) {
  const t = useT();
  const [items, setItems] = useState<AlertItem[]>([]);
  const [unreadOnly, setUnreadOnly] = useState(true);

  const load = () =>
    dashboardApi
      .alerts(org.id, unreadOnly)
      .then((inbox) => {
        setItems(inbox.items);
        onUnread(inbox.unread);
      })
      .catch(() => {});

  useEffect(() => {
    void load();
    const timer = setInterval(load, 30_000); // new alerts arrive as records get verified
    return () => clearInterval(timer);
  }, [org.id, unreadOnly]); // `load` closes over these

  async function acknowledge(id: string) {
    await dashboardApi.acknowledge(org.id, id).catch(() => {});
    void load();
  }

  return (
    <section className="dash-card">
      <div className="row center" style={{ marginBottom: "0.5rem" }}>
        <h2 className="grow dash-h2" style={{ margin: 0 }}><Bell size={18} aria-hidden /> {t("alertsTitle")}</h2>
        <button className="link small" onClick={() => setUnreadOnly(!unreadOnly)}>
          {unreadOnly ? t("alertsShowAll") : t("alertsShowUnread")}
        </button>
      </div>
      {items.length === 0 ? (
        <p className="dash-note">{t("alertsNone")}</p>
      ) : (
        <ul className="dash-list">
          {items.map((a) => {
            const minutes = a.verified_at
              ? Math.max(0, Math.round((Date.parse(a.created_at) - Date.parse(a.verified_at)) / 60_000))
              : null;
            return (
              <li key={a.id} className={a.acknowledged_at ? "seen" : ""}>
                <span className={`chip alert-${a.kind}`}>{t(`alertKind_${a.kind}`)}</span>
                <div className="grow">
                  <div>
                    {taxonName(a.payload.taxon)} <span className="dash-note">{t("alertInRegion", { region: a.payload.region.name })}</span>
                  </div>
                  <div className="meta">
                    {new Date(a.observed_at).toLocaleDateString(t.locale)}
                    {minutes != null && ` · ${t("alertAfter", { n: minutes })}`}
                  </div>
                </div>
                {a.acknowledged_at ? (
                  <span className="dash-note">{t("alertSeen")}</span>
                ) : (
                  <button className="link small" onClick={() => acknowledge(a.id)}>{t("alertAck")}</button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// --- Analyst -----------------------------------------------------------------------------

function DataTable({ result }: { result: NonNullable<AnalystAnswer["result"]> }) {
  const t = useT();
  if (!result.rows.length) return <p className="dash-note">{t("dashNone")}</p>;
  return (
    <table className="dash-table">
      <thead>
        <tr>{result.columns.map((c) => <th key={c} className={c === "key" ? "" : "num"}>{c === "key" ? "" : c.replace("_", " ")}</th>)}</tr>
      </thead>
      <tbody>
        {result.rows.map((row, i) => (
          <tr key={i}>
            {result.columns.map((c) => (
              <td key={c} className={c === "key" ? "" : "num"}>
                {row[c] == null ? "–" : c === "change_pct" ? `${Number(row[c]) > 0 ? "+" : ""}${row[c]}%` : String(row[c])}
                {c === "key" && row.common_name && <span className="dash-note"> · {String(row.common_name)}</span>}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AnalystPanel({ org }: { org: Org }) {
  const t = useT();
  const [question, setQuestion] = useState("");
  const [current, setCurrent] = useState<AnalystAnswer | null>(null);
  const [history, setHistory] = useState<AnalystAnswer[]>([]);
  const [busy, setBusy] = useState(false);
  const [showData, setShowData] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dashboardApi.history(org.id).then(setHistory).catch(() => {});
    setCurrent(null);
  }, [org.id]);

  async function ask(text: string) {
    if (text.trim().length < 3) return;
    setBusy(true);
    setError(null);
    setShowData(false);
    try {
      const answer = await dashboardApi.ask(org.id, text.trim());
      setCurrent(answer);
      setHistory((h) => [answer, ...h].slice(0, 20));
      setQuestion("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("err_generic"));
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  const examples: MessageKey[] = ["analystEx1", "analystEx2", "analystEx3"];
  return (
    <section className="dash-card stack">
      <h2 className="dash-h2" style={{ margin: 0 }}><Sparkles size={18} aria-hidden /> {t("analystTitle")}</h2>
      <form className="row" onSubmit={submit}>
        <input
          className="grow"
          style={{ marginTop: 0 }}
          value={question}
          maxLength={500}
          placeholder={t("analystPlaceholder")}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={busy}
        />
        <button className="primary" disabled={busy || question.trim().length < 3}>
          {busy ? t("analystThinking") : t("analystAsk")}
        </button>
      </form>
      {!current && !busy && (
        <div className="row center small">
          <span className="dash-note">{t("analystExamples")}</span>
          {examples.map((key) => (
            <button key={key} className="chip example" onClick={() => ask(t(key))}>{t(key)}</button>
          ))}
        </div>
      )}
      {error && <p className="error small">{error}</p>}
      {current?.answer && (
        <div className="analyst-answer stack tight">
          <p className="dash-note">“{current.question}”</p>
          {current.plan?.interpretation && (
            <p className="dash-note">{t("analystUnderstood", { text: current.plan.interpretation })}</p>
          )}
          <p>{current.answer.answer}</p>
          {current.answer.highlights.length > 0 && (
            <ul>{current.answer.highlights.map((h, i) => <li key={i}>{h}</li>)}</ul>
          )}
          {current.answer.caveats.map((c, i) => <p key={i} className="dash-note">⚠️ {c}</p>)}
          {current.result && current.result.columns.length > 0 && (
            <>
              <button className="link small" onClick={() => setShowData(!showData)}>
                {showData ? t("analystHideData") : t("analystShowData")}
              </button>
              {showData && <DataTable result={current.result} />}
            </>
          )}
          <p className="dash-note">{t("analystPowered", { model: current.model ?? "Gemini" })}</p>
        </div>
      )}
      {history.length > 0 && (
        <details>
          <summary className="small">{t("analystHistory")}</summary>
          <ul className="plain small">
            {history.map((h) => (
              <li key={h.id}>
                <button className="link small" onClick={() => setCurrent(h)}>{h.question}</button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

// --- Reports -----------------------------------------------------------------------------

export function ReportsPanel({ org }: { org: Org }) {
  const t = useT();
  const [reports, setReports] = useState<Report[]>([]);
  const [open, setOpen] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dashboardApi.reports(org.id).then(setReports).catch(() => {});
  }, [org.id]);

  async function create(period: "week" | "month" | "quarter") {
    setBusy(true);
    setError(null);
    try {
      const report = await dashboardApi.createReport(org.id, period);
      setReports((r) => [report, ...r]);
      setOpen(report);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("err_generic"));
    } finally {
      setBusy(false);
    }
  }

  const fmt = (d: string) => new Date(d).toLocaleDateString(t.locale);
  return (
    <section className="dash-card stack">
      <h2 className="dash-h2" style={{ margin: 0 }}><FileText size={18} aria-hidden /> {t("reportsTitle")}</h2>
      <div className="row">
        {(["week", "month", "quarter"] as const).map((p) => (
          <button key={p} disabled={busy} onClick={() => create(p)}>
            {t(p === "week" ? "reportWeek" : p === "month" ? "reportMonth" : "reportQuarter")}
          </button>
        ))}
      </div>
      {busy && <p className="dash-note">{t("reportWriting")}</p>}
      {error && <p className="error small">{error}</p>}
      {reports.length === 0 ? (
        <p className="dash-note">{t("reportsNone")}</p>
      ) : (
        <ul className="plain">
          {reports.map((r) => (
            <li key={r.id} className="row center">
              <button className="link" onClick={() => setOpen(r)}>{r.content.title}</button>
              <span className="dash-note">{t("reportPeriod", { start: fmt(r.period_start), end: fmt(r.period_end) })}</span>
            </li>
          ))}
        </ul>
      )}
      {open && <ReportView report={open} onClose={() => setOpen(null)} />}
    </section>
  );
}

function ReportView({ report, onClose }: { report: Report; onClose: () => void }) {
  const t = useT();
  const fmt = (d: string) => new Date(d).toLocaleDateString(t.locale);

  function print() {
    document.body.classList.add("printing-report");
    window.print();
    document.body.classList.remove("printing-report");
  }

  // Rendered from structured JSON as React text: nothing from the model becomes HTML.
  return (
    <div className="report-overlay" role="dialog" aria-modal="true" aria-label={report.content.title}>
      <article className="report-view">
        <div className="row center no-print">
          <span className="grow" />
          <button onClick={print}>🖨️ {t("reportPrint")}</button>
          <button onClick={onClose}>{t("reportClose")}</button>
        </div>
        <h1>{report.content.title}</h1>
        <p className="dash-note">{t("reportPeriod", { start: fmt(report.period_start), end: fmt(report.period_end) })} · {t("appName")}</p>
        <p className="lead">{report.content.summary}</p>
        {report.content.sections.map((s, i) => (
          <section key={i}>
            <h2>{s.heading}</h2>
            {s.paragraphs.map((p, j) => <p key={j}>{p}</p>)}
          </section>
        ))}
        {report.content.recommendations.length > 0 && (
          <section>
            <h2>{t("reportRecommendations")}</h2>
            <ul>{report.content.recommendations.map((r, i) => <li key={i}>{r}</li>)}</ul>
          </section>
        )}
        <p className="dash-note">{t("reportWrittenBy", { model: report.model ?? "Gemini" })}</p>
      </article>
    </div>
  );
}

// --- Alert settings (Manage tab) ---------------------------------------------------------

export function AlertSettingsTab({ org }: { org: Org }) {
  const t = useT();
  const [prefs, setPrefs] = useState<AlertSettings | null>(null);
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const isAdmin = org.my_role === "admin";

  useEffect(() => {
    dashboardApi.alertSettings(org.id).then((p) => {
      setPrefs(p);
      setUrl(p.webhook_url ?? "");
    }).catch(() => {});
  }, [org.id]);

  async function save(changes: Partial<AlertSettings>) {
    setMessage(null);
    try {
      const next = await dashboardApi.saveAlertSettings(org.id, changes);
      setPrefs(next);
      setMessage(t("settingsSaved"));
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : t("err_generic"));
    }
  }

  if (!prefs) return null;
  const toggles: [keyof AlertSettings, MessageKey][] = [
    ["notify_invasive", "alertKind_invasive"],
    ["notify_new_record", "alertKind_new_record"],
    ["notify_priority", "alertKind_priority"],
  ];
  return (
    <div className="stack">
      <span className="small">{t("settingsNotify")}</span>
      {toggles.map(([key, label]) => (
        <label key={key} className="check">
          <input
            type="checkbox"
            disabled={!isAdmin}
            checked={Boolean(prefs[key])}
            onChange={(e) => save({ [key]: e.target.checked })}
          />
          {t(label)}
        </label>
      ))}
      <label>
        {t("settingsWebhook")}
        <input value={url} disabled={!isAdmin} placeholder="https://" onChange={(e) => setUrl(e.target.value)} />
      </label>
      <p className="dash-note">{t("settingsWebhookHint")}</p>
      {isAdmin ? (
        <div className="row">
          <button onClick={() => save({ webhook_url: url.trim() || null })}>{t("settingsSave")}</button>
          {prefs.webhook_url && (
            <>
              <button onClick={async () => setSecret((await dashboardApi.rotateSecret(org.id)).webhook_secret)}>
                {t("settingsRotate")}
              </button>
              <button
                onClick={async () => {
                  const r = await dashboardApi.testWebhook(org.id);
                  setMessage(r.ok ? t("settingsTestOk") : t("settingsTestFailed", { error: r.error ?? String(r.http_status) }));
                }}
              >
                {t("settingsTest")}
              </button>
            </>
          )}
        </div>
      ) : (
        <p className="dash-note">{t("dashAdminOnly")}</p>
      )}
      {secret && (
        <p className="small">
          {t("settingsSecretOnce")} <code>{secret}</code>
        </p>
      )}
      {message && <p className="small">{message}</p>}
    </div>
  );
}
