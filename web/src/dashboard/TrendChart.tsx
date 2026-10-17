import { useMemo, useState } from "react";

import { useT } from "../i18n";
import type { TaxonGroup } from "../types";
import { GROUPS } from "./api";
import type { TrendPoint } from "./types";

const W = 640;
const H = 240;
const PAD = { top: 12, right: 8, bottom: 26, left: 40 };
const GAP = 2; // surface gap between stacked segments
const RADIUS = 4;

function niceMax(value: number): number {
  if (value <= 5) return 5;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((m) => value <= m * magnitude) ?? 10;
  return step * magnitude;
}

/** Path for a rectangle with rounded top corners and a square base. */
function topRounded(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + radius} Q${x},${y} ${x + radius},${y} H${x + w - radius} Q${x + w},${y} ${x + w},${y + radius} V${y + h} Z`;
}

function monthLabel(month: string, locale: string): string {
  const [year, m] = month.split("-").map(Number);
  return new Date(Date.UTC(year, m - 1, 1)).toLocaleDateString(locale, { month: "short", year: "2-digit" });
}

export function TrendChart({ data }: { data: TrendPoint[] }) {
  const t = useT();
  const [asTable, setAsTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);

  const max = useMemo(() => niceMax(Math.max(1, ...data.map((d) => d.observations))), [data]);
  const ticks = [0, max / 4, max / 2, (3 * max) / 4, max];
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const band = innerW / Math.max(data.length, 1);
  const barW = Math.min(24, band * 0.6);
  const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
  const labelEvery = Math.ceil(data.length / 8);
  const groupLabel = (g: TaxonGroup) => t(`group_${g}_one`);

  if (data.length === 0) return <p className="dash-note">{t("dashNone")}</p>;

  return (
    <div className="trend">
      <div className="dash-legend">
        {GROUPS.map((g) => (
          <span key={g}><span className={`key rect key-${g}`} />{groupLabel(g)}</span>
        ))}
        <button className="link small" style={{ marginLeft: "auto" }} onClick={() => setAsTable(!asTable)}>
          {asTable ? t("dashShowChart") : t("dashShowTable")}
        </button>
      </div>

      {asTable ? (
        <table className="dash-table">
          <thead>
            <tr>
              <th>{t("dashMonth")}</th>
              {GROUPS.map((g) => <th key={g} className="num">{groupLabel(g)}</th>)}
              <th className="num">{t("dashTotal")}</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.month}>
                <td>{monthLabel(d.month, t.locale)}</td>
                {GROUPS.map((g) => <td key={g} className="num">{(d.by_group[g] ?? 0).toLocaleString(t.locale)}</td>)}
                <td className="num">{d.observations.toLocaleString(t.locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("dashTrends")}>
          <g className="grid">
            {ticks.map((v) => <line key={v} x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />)}
          </g>
          <g className="axis">
            {ticks.map((v) => (
              <text key={v} x={PAD.left - 6} y={y(v) + 4} textAnchor="end">{v.toLocaleString(t.locale)}</text>
            ))}
            {data.map((d, i) =>
              i % labelEvery === 0 ? (
                <text key={d.month} x={PAD.left + band * (i + 0.5)} y={H - 6} textAnchor="middle">
                  {monthLabel(d.month, t.locale)}
                </text>
              ) : null,
            )}
          </g>
          <line className="baseline" x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} />
          {data.map((d, i) => {
            const x = PAD.left + band * i + (band - barW) / 2;
            const present = GROUPS.filter((g) => (d.by_group[g] ?? 0) > 0);
            let base = 0;
            return (
              <g
                key={d.month}
                className="col"
                tabIndex={0}
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
              >
                {/* The whole band is the hit target, bigger than the bar. */}
                <rect className="hit" x={PAD.left + band * i} y={PAD.top} width={band} height={innerH} />
                {present.map((g, j) => {
                  const value = d.by_group[g] ?? 0;
                  const top = y(base + value);
                  const bottom = y(base) - (j > 0 ? GAP : 0);
                  base += value;
                  const h = Math.max(bottom - top, 1);
                  const isTop = j === present.length - 1;
                  return isTop ? (
                    <path key={g} className={`seg seg-${g}`} d={topRounded(x, top, barW, h, RADIUS)} />
                  ) : (
                    <rect key={g} className={`seg seg-${g}`} x={x} y={top} width={barW} height={h} />
                  );
                })}
              </g>
            );
          })}
        </svg>
      )}

      {!asTable && hover !== null && data[hover] && (
        <div
          className="trend-tip"
          style={{ left: `${((PAD.left + band * (hover + 0.5)) / W) * 100}%`, top: `${(y(data[hover].observations) / H) * 100}%` }}
        >
          <div className="small">{monthLabel(data[hover].month, t.locale)}</div>
          {GROUPS.map((g) => (
            <div key={g} className="row">
              <span><span className={`key-line key-${g}`} />{groupLabel(g)}</span>
              <strong>{(data[hover].by_group[g] ?? 0).toLocaleString(t.locale)}</strong>
            </div>
          ))}
          <div className="row"><span>{t("dashTotal")}</span><strong>{data[hover].observations.toLocaleString(t.locale)}</strong></div>
        </div>
      )}
    </div>
  );
}
