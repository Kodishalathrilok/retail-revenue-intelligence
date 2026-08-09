'use client';

import { useEffect, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { CaveatBar } from '@/components/Caveats';
import { fmtMoney, fmtNum, get } from '@/lib/api';

type Overview = {
  total_revenue: number; total_baskets: number; total_households: number;
  avg_basket_value: number; total_units: number; weeks_covered: number;
};
type Weekly = { week_no: number; revenue: string; baskets: number;
  rolling_7wk_avg: string; is_partial_week: boolean };
type Segment = { segment: string; households: number; pct_of_revenue: string;
  segment_revenue: string; avg_baskets: string };

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

export default function OverviewPage() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [weekly, setWeekly] = useState<Weekly[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [departments, setDepartments] = useState<string[]>([]);
  const [dept, setDept] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    get<{ items: { department: string }[] }>('/api/v1/departments')
      .then((d) => setDepartments(d.items.map((x) => x.department)))
      .catch(() => {});
    get<{ items: Segment[] }>('/api/v1/segments/rfm')
      .then((d) => setSegments(d.items)).catch(() => {});
  }, []);

  // Drill-down: department filter re-queries the server rather than filtering
  // client-side, so the numbers are always computed by SQL.
  useEffect(() => {
    setLoading(true);
    const q = dept ? `?department=${encodeURIComponent(dept)}` : '';
    Promise.all([
      get<Overview>(`/api/v1/overview${q}`),
      get<{ items: Weekly[] }>(`/api/v1/revenue/weekly${q}`),
    ])
      .then(([o, w]) => { setOv(o); setWeekly(w.items); setError(null); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [dept]);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Executive overview</h1>
          <p className="mt-1 text-sm text-slate-500">
            {dept ? `Filtered to ${dept}` : 'All departments'}
          </p>
        </div>
        <label className="text-sm">
          <span className="mr-2 text-slate-500">Drill down</span>
          <select
            value={dept}
            onChange={(e) => setDept(e.target.value)}
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All departments</option>
            {departments.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error} — is the API running? <code>rrip serve</code>
        </div>
      )}

      {ov && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
          <Stat label="Revenue" value={fmtMoney(ov.total_revenue)} sub="net of retail discount" />
          <Stat label="Baskets" value={fmtNum(ov.total_baskets)} />
          <Stat label="Households" value={fmtNum(ov.total_households)} />
          <Stat label="Avg basket" value={fmtMoney(ov.avg_basket_value)} />
          <Stat label="Units" value={fmtNum(ov.total_units)} sub="excludes weighted goods" />
        </div>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold">Weekly revenue</h2>
        <p className="mb-3 text-xs text-slate-500">
          Seven-week centred rolling average. A trailing average would lag the
          series by half its width and misplace turning points.
        </p>
        {loading && <div className="py-16 text-center text-sm text-slate-400">Loading…</div>}
        {!loading && weekly.length > 0 && (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={weekly.map((w) => ({
              week: w.week_no,
              revenue: Number(w.revenue),
              trend: w.rolling_7wk_avg ? Number(w.rolling_7wk_avg) : null,
              partial: w.is_partial_week,
            }))}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="week" tick={{ fontSize: 11 }}
                     label={{ value: 'week', position: 'insideBottom', offset: -4, fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
              <Tooltip formatter={(v: number) => fmtMoney(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="revenue" stroke="#94a3b8" dot={false} name="weekly" />
              <Line type="monotone" dataKey="trend" stroke="#0f172a" dot={false}
                    strokeWidth={2} name="7-week centred avg" />
            </LineChart>
          </ResponsiveContainer>
        )}
        <p className="mt-2 text-xs text-slate-400">
          Weeks 1 and 102 are 5 and 6 days rather than 7; their totals are not
          comparable to a full week.
        </p>
      </section>

      {segments.length > 0 && (
        <section className="rounded-lg border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold">Revenue by RFM segment</h2>
          <p className="mb-3 text-xs text-slate-500">
            Quintile scores on recency, frequency and monetary value. Recency is
            measured against panel end, not today.
          </p>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={segments.map((s) => ({
              segment: s.segment, revenue: Number(s.segment_revenue),
              share: Number(s.pct_of_revenue), households: s.households,
            }))} margin={{ bottom: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="segment" tick={{ fontSize: 10 }} angle={-20}
                     textAnchor="end" interval={0} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
              <Tooltip formatter={(v: number, n) => n === 'revenue' ? fmtMoney(v) : v} />
              <Bar dataKey="revenue" fill="#0f172a" name="revenue" />
            </BarChart>
          </ResponsiveContainer>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            {segments.slice(0, 4).map((s) => (
              <div key={s.segment} className="rounded border border-slate-100 p-2">
                <div className="font-medium">{s.segment}</div>
                <div className="text-slate-500">
                  {fmtNum(s.households)} households · {s.pct_of_revenue}% of revenue
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <CaveatBar calendar panel revenue />
    </div>
  );
}
