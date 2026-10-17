import "./console.css";

import { ExternalLink, LogOut } from "lucide-react";
import type { ReactNode } from "react";

import type { Session } from "../api";
import { AuthScreen } from "../components/AuthScreen";
import { isLocale, LOCALES, useT, type Locale } from "../i18n";

export interface NavItem<K extends string> {
  key: K;
  label: string;
  icon: ReactNode;
  badge?: number;
}

interface ShellProps<K extends string> {
  /** "Organisation dashboard" or "Admin console". */
  product: string;
  nav: NavItem<K>[];
  active: K;
  onNavigate: (key: K) => void;
  /** Extra sidebar content under the brand, e.g. the organisation switcher. */
  sidebarTop?: ReactNode;
  session: Session;
  locale: Locale;
  onLocale: (locale: Locale) => void;
  onSignOut: () => void;
  links?: { href: string; label: string }[];
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

/** Desktop console layout shared by the organisation dashboard and the admin console. */
export function ConsoleShell<K extends string>(props: ShellProps<K>) {
  const t = useT();
  const { session } = props;
  return (
    <div className="dash console">
      <aside className="console-side">
        <a className="console-brand" href={props.links?.[0]?.href ?? "/"}>
          <img src="/icon.svg" alt="" />
          <span>
            <strong>{t("appName")}</strong>
            <small>{props.product}</small>
          </span>
        </a>
        {props.sidebarTop}
        <nav className="console-nav" aria-label={props.product}>
          {props.nav.map((item) => (
            <button
              key={item.key}
              className={item.key === props.active ? "active" : ""}
              aria-current={item.key === props.active ? "page" : undefined}
              onClick={() => props.onNavigate(item.key)}
            >
              {item.icon}
              <span className="grow">{item.label}</span>
              {!!item.badge && <span className="console-badge">{item.badge}</span>}
            </button>
          ))}
        </nav>
        <div className="console-foot">
          {props.links?.map((link) => (
            <a key={link.href} href={link.href}>
              <ExternalLink size={15} aria-hidden /> {link.label}
            </a>
          ))}
          <select
            value={props.locale}
            aria-label={t("language")}
            onChange={(e) => isLocale(e.target.value) && props.onLocale(e.target.value)}
          >
            {Object.entries(LOCALES).map(([code, label]) => (
              <option key={code} value={code}>{label}</option>
            ))}
          </select>
          <div className="console-user">
            <span className="console-avatar" aria-hidden>{session.user.display_name[0]?.toUpperCase()}</span>
            <span className="grow">
              <strong>{session.user.display_name}</strong>
              <small>{session.user.email}</small>
            </span>
            <button className="icon-button" onClick={props.onSignOut} aria-label={t("signOut")} title={t("signOut")}>
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="console-main">
        <header className="console-head">
          <div className="grow">
            <h1>{props.title}</h1>
            {props.subtitle && <p className="dash-note">{props.subtitle}</p>}
          </div>
          {props.actions}
        </header>
        {props.children}
      </div>
    </div>
  );
}

/** Split-screen sign-in for the desktop consoles. */
export function ConsoleSignIn({ product, lead, points, onAuthed, locale, onLocale }: {
  product: string;
  lead: string;
  points: { icon: ReactNode; text: string }[];
  onAuthed: (session: Session) => void;
  locale: Locale;
  onLocale: (locale: Locale) => void;
}) {
  const t = useT();
  return (
    <div className="dash console-signin">
      <section className="console-hero">
        <div className="console-brand">
          <img src="/icon.svg" alt="" />
          <span>
            <strong>{t("appName")}</strong>
            <small>{product}</small>
          </span>
        </div>
        <div className="console-hero-body">
          <h1>{product}</h1>
          <p>{lead}</p>
          <ul>
            {points.map((p) => (
              <li key={p.text}><span className="console-hero-icon">{p.icon}</span>{p.text}</li>
            ))}
          </ul>
        </div>
        <p className="console-hero-foot">{t("tagline")}</p>
      </section>
      <section className="console-form">
        <div className="console-form-top">
          <select value={locale} aria-label={t("language")} onChange={(e) => isLocale(e.target.value) && onLocale(e.target.value)}>
            {Object.entries(LOCALES).map(([code, label]) => (
              <option key={code} value={code}>{label}</option>
            ))}
          </select>
        </div>
        <div className="console-form-body">
          <h2>{t("signIn")}</h2>
          <AuthScreen compact onAuthed={onAuthed} />
        </div>
      </section>
    </div>
  );
}

/** A centred message card inside the console area (empty states, no access). */
export function ConsoleMessage({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="console-message">
      <span className="icon-circle large">{icon}</span>
      <div>{children}</div>
    </div>
  );
}
