import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import geocomplyLogo from "./assets/geocomply-logo.png";
import "./styles.css";

const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
type Customer = { customer: string; available_from: string; available_to: string };
type Opportunity = { customer: string; scope: { from: string; to: string }; current_pair?: string; proposed_pair?: string; modeled_savings_per_1000_eligible_entrants_usd?: string; calibration_sample_size?: number; proposed_order_cheaper_week_count?: number; comparable_week_count?: number; result: string; reason: string };
type Vendor = { vendor_id: string; vendor_name: string; unit_price_usd: string; checks_sent: number; match_rate: string | null; timeout_rate: string | null };
type RecommendationVendor = { vendor_id: string; vendor_name: string; price_per_check_usd: string; checks_sent: number; matched: number; timeout: number; match_rate: string; timeout_rate: string };
type VendorRecommendation = { status: "available" | "unavailable"; reason?: string; customer: string; regions: string[]; scope: { from: string; to: string }; current_first_vendor: string | null; eligible_vendors: RecommendationVendor[]; unavailable_options: Array<{ vendor_id: string; vendor_name: string; reason: string }>; note: string; how_it_works?: { formula: string; normalization: string } };
type ScoredRecommendationVendor = RecommendationVendor & { metricScores: { cost: number; match: number; timeout: number }; contributions: { cost: number; match: number; timeout: number }; score: number };
type Comparison = { price_threshold?: { headline: string; detail: string; assumption: string; formula: string }; status: "available" | "unavailable"; reason?: string; calibration?: { sample_size: number; vendors: Vendor[] }; current_order?: Array<{ vendor_id: string; vendor_name: string }>; alternative_order?: Array<{ vendor_id: string; vendor_name: string }>; model?: { explanation: string; formula: string; current_order_cost_per_1000_eligible_entrants_usd: string; alternative_order_cost_per_1000_eligible_entrants_usd: string; pair_cost_reduction_pct: string | null }; weekly_consistency?: { weeks: Array<{ week_starting: string; savings_per_entrant_usd: string }>; statement: string }; pilot_economics?: { vendor_savings_per_1000_eligible_entrants_usd: string; projected_vendor_savings_usd: string | null; savings_after_implementation_usd: string | null; break_even_eligible_volume: number | null; beneficiary_label: string; recorded_period: { eligible_verification_volume: number; scope: { from: string; to: string }; assumption: string } }; decision_summary?: { headline: string; detail: string; limitation: string } };
type ExportLinks = { json_export_url: string; html_export_url: string };
const money = (value: string | null | undefined, precision = 0) => value == null ? "Not available" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: precision, maximumFractionDigits: precision }).format(Number(value));
const outcome = (value: string | null | undefined) => value == null ? "Not available" : Number(value) < 0 ? `Additional cost ${money(String(Math.abs(Number(value))), 2)}` : money(value, 2);
const formatNumber = (value: number | string) => new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value));
const rate = (value: string | null) => value == null ? "Not available" : `${formatNumber(Number(value) * 100)}%`;
function InfoTip({ text }: { text: string }) {
  return <span className="info-tip" tabIndex={0} aria-label={text}><span aria-hidden="true">?</span><span className="tooltip" role="tooltip">{text}</span></span>;
}
function HighlightNumbers({ text }: { text: string }) {
  return <>{text.split(/(\$-?[\d,]+(?:\.\d+)?|-?[\d,]+(?:\.\d+)?%)/g).map((part, index) => /^\$?-?[\d,]+(?:\.\d+)?%?$/.test(part) ? <mark className="number-pill" key={index}>{part}</mark> : part)}</>;
}
const priorityPercent = (value: number, total: number) => total === 0 ? "0.00%" : `${(value / total * 100).toFixed(2)}%`;
const importanceLabel = (value: number) => ["Ignore", "Very low", "Low", "Medium", "High", "Very high"][value] ?? "Ignore";

function VendorRecommendationPanel({ data, error, loading, priorities, setPriorities, scored, pairVendorIds }: { data: VendorRecommendation | null; error: string; loading: boolean; priorities: { cost: number; match: number; timeout: number }; setPriorities: (value: { cost: number; match: number; timeout: number }) => void; scored: { total: number; vendors: ScoredRecommendationVendor[]; winners: string[] }; pairVendorIds: string[] }) {
  const controls: Array<{ key: "cost" | "match" | "timeout"; label: string; help: string }> = [
    { key: "cost", label: "Lower Check Cost", help: "Lower vendor price per lookup" },
    { key: "match", label: "Higher First-Step Match", help: "More people matched by the first vendor" },
    { key: "timeout", label: "Fewer First-Step Timeouts", help: "Fewer first-vendor checks timing out" },
  ];
  const winnerVendors = scored.vendors.filter(item => scored.winners.includes(item.vendor_id));
  const current = scored.vendors.find(item => item.vendor_id === data?.current_first_vendor);
  const primary = winnerVendors[0];
  const outsideExistingPair = winnerVendors.some(item => !pairVendorIds.includes(item.vendor_id));
  const explanation = (() => {
    if (!primary || !current || winnerVendors.length !== 1) return winnerVendors.length > 1 ? "These vendors are tied under the selected priorities; no single winner is being forced." : "The recorded first vendor has no eligible calibration evidence for a direct trade-off.";
    if (primary.vendor_id === current.vendor_id) return `Keep ${current.vendor_name} first under your selected priorities.`;
    const price = Number(primary.price_per_check_usd) - Number(current.price_per_check_usd);
    const match = (Number(primary.match_rate) - Number(current.match_rate)) * 100;
    const timeout = (Number(primary.timeout_rate) - Number(current.timeout_rate)) * 100;
    const changes = [
      price === 0 ? null : `costs ${money(String(Math.abs(price)), 2)} ${price > 0 ? "more" : "less"} per check`,
      match === 0 ? null : `matches ${Math.abs(match).toFixed(2)} percentage points ${match > 0 ? "more" : "fewer"} of the calibration sample`,
      timeout === 0 ? null : `has a ${Math.abs(timeout).toFixed(2)}-point ${timeout > 0 ? "higher" : "lower"} timeout rate`,
    ].filter(Boolean);
    return changes.length ? `Consider ${primary.vendor_name} for a first-position pilot. Compared with ${current.vendor_name}, it ${changes.join(", and ")}.` : `Consider ${primary.vendor_name} for a first-position pilot. Its observed metrics are the same as ${current.vendor_name}.`;
  })();
  return <section className={`vendor-recommendation ${loading ? "is-loading" : ""}`} aria-labelledby="vendor-recommendation-title" aria-busy={loading}>
    <div className="recommendation-heading"><div><p>{priorities.cost === 3 && priorities.match === 3 && priorities.timeout === 3 ? "Equal priorities" : "Priority-based view"}</p><h3 id="vendor-recommendation-title">Vendor Recommendation</h3></div><button className="reset-priorities" onClick={() => setPriorities({ cost: 3, match: 3, timeout: 3 })}>Reset</button></div>
    {loading && <p className="recommendation-loading" aria-live="polite">Refreshing calibration evidence…</p>}
    {error && <p className="recommendation-unavailable">{error}</p>}
    {data?.status === "unavailable" && <p className="recommendation-unavailable">{data.reason}</p>}
    {data?.status === "available" && <>
      <div className="priority-controls">{controls.map(control => <label key={control.key}><span><b>{control.label}</b><small>{control.help}</small></span><div><input type="range" min="0" max="5" step="1" value={priorities[control.key]} onChange={event => setPriorities({ ...priorities, [control.key]: Number(event.target.value) })} /><output>{priorities[control.key]} · {importanceLabel(priorities[control.key])}</output></div></label>)}</div>
      <div className="recommendation-result"><span>Recommended First-Vendor Pilot</span>{scored.total === 0 ? <strong>Choose at least one priority</strong> : <strong>{winnerVendors.map(item => item.vendor_name).join(" · ")}</strong>}<p>{scored.total === 0 ? "Move one or more priority sliders to rank the available vendors." : explanation}</p>{outsideExistingPair && scored.total > 0 && <p className="unmodeled-savings">This vendor ranks highest for a first-position pilot. Savings for a waterfall using this vendor have not been modeled.</p>}<small>{data.customer} · {data.regions.join(", ")} · {data.scope.from} to {data.scope.to}<br />Calibration sample: {scored.vendors.map(item => `${item.vendor_name} ${formatNumber(item.checks_sent)}`).join(" · ")} checks</small></div>
      <div className="recommendation-table-wrap"><table className="recommendation-table"><thead><tr><th>Vendor</th><th>Price/check</th><th>First-step match</th><th>Timeout rate</th><th>Score</th></tr></thead><tbody>{scored.vendors.map(vendor => <tr className={scored.winners.includes(vendor.vendor_id) ? "recommended" : ""} key={vendor.vendor_id}><th>{vendor.vendor_name}<small>{vendor.vendor_id}</small></th><td>{money(vendor.price_per_check_usd, 2)}</td><td>{rate(vendor.match_rate)}</td><td>{rate(vendor.timeout_rate)}</td><td><b>{scored.total === 0 ? "—" : formatNumber(vendor.score)}</b></td></tr>)}</tbody></table></div>
      <p className="recommendation-note">{data.note}</p>
      <details className="recommendation-how"><summary>How this recommendation works</summary><p>Normalized priorities: Cost {priorityPercent(priorities.cost, scored.total)} · Match {priorityPercent(priorities.match, scored.total)} · Timeout {priorityPercent(priorities.timeout, scored.total)}.</p><p>{data.how_it_works?.formula}</p><p>{data.how_it_works?.normalization} Selected priorities are user assumptions.</p><div>{scored.vendors.map(vendor => <span key={vendor.vendor_id}><b>{vendor.vendor_name}</b> · Cost {formatNumber(vendor.contributions.cost)} + Match {formatNumber(vendor.contributions.match)} + Timeout {formatNumber(vendor.contributions.timeout)}</span>)}</div></details>
      {data.unavailable_options.length > 0 && <p className="unavailable-options">Unavailable: {data.unavailable_options.map(item => `${item.vendor_name} — ${item.reason}`).join(" · ")}</p>}
    </>}
  </section>;
}

function App() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [customer, setCustomer] = useState(""); const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const [volume, setVolume] = useState("10000"); const [volumeMode, setVolumeMode] = useState<"recorded" | "custom">("recorded");
  const [implementationCost, setImplementationCost] = useState("0"); const [notes, setNotes] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null); const [loading, setLoading] = useState(false);
  const [vendorRecommendation, setVendorRecommendation] = useState<VendorRecommendation | null>(null); const [recommendationError, setRecommendationError] = useState(""); const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [priorities, setPriorities] = useState({ cost: 3, match: 3, timeout: 3 });
  const [activeArea, setActiveArea] = useState<"opportunities" | "comparison" | "business">("opportunities");
  const [screen, setScreen] = useState<"landing" | "workspace">("landing");
  const [error, setError] = useState(""); const [exports, setExports] = useState<ExportLinks | null>(null);
  const hasNavigated = useRef(false);
  useEffect(() => {
    Promise.all([fetch(`${API}/customers`).then(r => r.json()), fetch(`${API}/opportunities`).then(r => r.json())]).then(([customerData, opportunityData]) => {
      setCustomers(customerData.customers); setOpportunities(opportunityData.opportunities);
      const first = customerData.customers[0]; if (first) { setCustomer(first.customer); setFrom(first.available_from); setTo(first.available_to); }
    }).catch(() => setError("Could not load customer evidence."));
  }, []);
  useEffect(() => {
    const enteredVolume = Number(volume), enteredCost = Number(implementationCost);
    if (!customer || !from || !to || !Number.isInteger(enteredVolume) || enteredVolume < 0 || enteredCost < 0 || !Number.isFinite(enteredCost)) return;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const response = await fetch(`${API}/compare-pair`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ customer, from, to, projected_eligible_entrants: enteredVolume, implementation_cost_usd: enteredCost }) });
        const result = await response.json(); if (!response.ok) throw new Error(result.detail ?? "Could not compare this scope.");
        setComparison(result);
        if (volumeMode === "recorded" && result.status === "available") { const recorded = String(result.pilot_economics.recorded_period.eligible_verification_volume); if (recorded !== volume) setVolume(recorded); }
      } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not compare this scope."); } finally { setLoading(false); }
    }, 180);
    return () => window.clearTimeout(timer);
  }, [customer, from, to, volume, implementationCost, volumeMode]);
  useEffect(() => {
    if (!customer || !from || !to) return;
    const controller = new AbortController();
    setRecommendationLoading(true);
    fetch(`${API}/vendor-recommendation?customer=${encodeURIComponent(customer)}&from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`, { signal: controller.signal })
      .then(async response => { const result = await response.json(); if (!response.ok) throw new Error(result.detail ?? "Could not load vendor recommendation evidence."); return result; })
      .then(result => { setVendorRecommendation(result); setRecommendationError(""); })
      .catch(caught => { if ((caught as Error).name !== "AbortError") setRecommendationError(caught instanceof Error ? caught.message : "Could not load vendor recommendation evidence."); })
      .finally(() => { if (!controller.signal.aborted) setRecommendationLoading(false); });
    return () => controller.abort();
  }, [customer, from, to]);
  useEffect(() => {
    if (!hasNavigated.current) { hasNavigated.current = true; return; }
    const frame = window.requestAnimationFrame(() => document.querySelector<HTMLElement>(".workspace-guide")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return () => window.cancelAnimationFrame(frame);
  }, [activeArea]);
  const ranked = useMemo(() => [...opportunities].sort((a, b) => Number(b.modeled_savings_per_1000_eligible_entrants_usd ?? -Infinity) - Number(a.modeled_savings_per_1000_eligible_entrants_usd ?? -Infinity)), [opportunities]);
  const scoredVendors = useMemo(() => {
    const vendors = vendorRecommendation?.eligible_vendors ?? [];
    const total = priorities.cost + priorities.match + priorities.timeout;
    if (!vendors.length) return { total, vendors: [], winners: [] as string[] };
    const values = { cost: vendors.map(item => Number(item.price_per_check_usd)), match: vendors.map(item => Number(item.match_rate)), timeout: vendors.map(item => Number(item.timeout_rate)) };
    const normalized = (value: number, metric: "cost" | "match" | "timeout", higherIsBetter: boolean) => {
      const low = Math.min(...values[metric]), high = Math.max(...values[metric]);
      return high === low ? 1 : (higherIsBetter ? value - low : high - value) / (high - low);
    };
    const scored = vendors.map(vendor => {
      const metricScores = { cost: normalized(Number(vendor.price_per_check_usd), "cost", false), match: normalized(Number(vendor.match_rate), "match", true), timeout: normalized(Number(vendor.timeout_rate), "timeout", false) };
      const contributions = { cost: total ? priorities.cost / total * metricScores.cost * 100 : 0, match: total ? priorities.match / total * metricScores.match * 100 : 0, timeout: total ? priorities.timeout / total * metricScores.timeout * 100 : 0 };
      return { ...vendor, metricScores, contributions, score: contributions.cost + contributions.match + contributions.timeout };
    }).sort((a, b) => b.score - a.score);
    const winners = total === 0 ? [] : scored.filter(item => Math.abs(item.score - scored[0].score) < 1e-9).map(item => item.vendor_id);
    return { total, vendors: scored, winners };
  }, [vendorRecommendation, priorities]);
  const pilot = comparison?.pilot_economics;
  const choose = (name: string, scope?: { from: string; to: string }) => { const selected = customers.find(item => item.customer === name); setCustomer(name); setFrom(scope?.from ?? selected?.available_from ?? ""); setTo(scope?.to ?? selected?.available_to ?? ""); setVolumeMode("recorded"); setExports(null); setActiveArea("comparison"); };
  const setMode = (mode: "recorded" | "custom") => { setVolumeMode(mode); if (mode === "recorded" && pilot) setVolume(String(pilot.recorded_period.eligible_verification_volume)); };
  const exportProposal = async () => {
    setLoading(true);
    try {
      const scenarioResponse = await fetch(`${API}/scenarios`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: `${customer} proposal · ${from} to ${to}`, scenario_type: "pair_comparison", customer, from, to, payload: { projected_eligible_entrants: Number(volume), volume_mode: volumeMode, implementation_cost_usd: Number(implementationCost), implementation_notes: notes.split("\n").map(note => note.trim()).filter(Boolean), weighted_first_vendor_ranking: { priorities, normalized_priorities: { cost: priorityPercent(priorities.cost, scoredVendors.total), match: priorityPercent(priorities.match, scoredVendors.total), timeout: priorityPercent(priorities.timeout, scoredVendors.total) }, recommended_vendor_ids: scoredVendors.winners, ranked_vendors: scoredVendors.vendors.map(vendor => ({ vendor_id: vendor.vendor_id, vendor_name: vendor.vendor_name, score: vendor.score.toFixed(6) })), note: "This first-vendor ranking is separate from the pair-swap economics and does not model waterfall savings." } } }) });
      const scenario = await scenarioResponse.json(); if (!scenarioResponse.ok) throw new Error(scenario.detail);
      const proposalResponse = await fetch(`${API}/proposals`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario_id: scenario.id }) });
      const proposal = await proposalResponse.json(); if (!proposalResponse.ok) throw new Error(proposal.detail);
      setExports({ json_export_url: proposal.json_export_url, html_export_url: proposal.html_export_url });
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create proposal."); } finally { setLoading(false); }
  };
  return <main className={screen === "landing" ? "landing-shell" : "workspace-shell"}>
    {screen === "landing" ? <section className="landing-page" aria-labelledby="landing-title">
      <div className="landing-brand"><img className="geocomply-wordmark" src={geocomplyLogo} alt="GeoComply" /><b>· IDComply</b></div>
      <div className="landing-hero"><p>Decision workbench</p><h1 id="landing-title">IDComply<br />Waterfall Pricing</h1><span>Compare eligible registry-vendor orders, review the evidence, and build a clear pilot case.</span><button className="landing-cta" onClick={() => setScreen("workspace")}>Open workbench <b>→</b></button></div>
      <div className="landing-outline" aria-label="What the workbench includes"><span><b>01</b> Choose an opportunity</span><span><b>02</b> Review the comparison</span><span><b>03</b> Evaluate the business case</span></div>
    </section> : <>
    <header className="topbar"><div className="topbar-brand"><img className="geocomply-wordmark" src={geocomplyLogo} alt="GeoComply" /><span><b>IDComply Waterfall Pricing</b><small>Decision workbench</small></span></div><button className="topbar-overview" onClick={() => setScreen("landing")}>Overview</button></header>
    <p className="scope-once">Compares the first two eligible registry vendors. Other steps stay unchanged.</p>{error && <p className="error">{error}</p>}
    <nav className="workspace-guide" aria-label="Decision workflow"><button className={activeArea === "opportunities" ? "guide-active" : ""} onClick={() => setActiveArea("opportunities")}><b>01</b><span>Choose opportunity<small>Select a customer and proposed pair.</small></span></button><button className={activeArea === "comparison" ? "guide-active" : ""} onClick={() => setActiveArea("comparison")}><b>02</b><span>Review comparison<small>See the evidence and recommendation.</small></span></button><button className={activeArea === "business" ? "guide-active" : ""} onClick={() => setActiveArea("business")}><b>03</b><span>Evaluate business case<small>Set volume, cost, and export.</small></span></button></nav>
      <section className="step-card opportunities-section" hidden={activeArea !== "opportunities"}><div className="step-heading"><h2>Opportunities</h2><label>Customer<select value={customer} onChange={event => choose(event.target.value)}>{customers.map(item => <option key={item.customer}>{item.customer}</option>)}</select></label><p className="scope">Source period: {from} to {to}</p></div><div className="opportunity-table"><div className="opportunity-row table-label"><span>Customer</span><span>Current / proposed</span><span>Savings / 1k</span><span>Consistency</span><span>Result</span></div>{ranked.map(item => <button className={`opportunity-row ${customer === item.customer ? "selected" : ""}`} key={item.customer} onClick={() => choose(item.customer, item.scope)}><span><b>{item.customer}</b><small>{item.calibration_sample_size == null ? "—" : formatNumber(item.calibration_sample_size)} calibration entrants</small></span><span>{item.current_pair ? <><b>Current: {item.current_pair}</b><small>Proposed: {item.proposed_pair}</small></> : item.reason}</span><span>{outcome(item.modeled_savings_per_1000_eligible_entrants_usd)}</span><span>{item.comparable_week_count ? `${formatNumber(item.proposed_order_cheaper_week_count ?? 0)}/${formatNumber(item.comparable_week_count)} weeks` : "—"}</span><span>{item.result}</span></button>)}</div></section>
      <section className="step-card comparison-section" hidden={activeArea !== "comparison"}><h2>Vendor comparison</h2><VendorRecommendationPanel data={vendorRecommendation} error={recommendationError} loading={recommendationLoading} priorities={priorities} setPriorities={setPriorities} scored={scoredVendors} pairVendorIds={comparison?.current_order?.map(vendor => vendor.vendor_id) ?? []} />{loading && <p className="comparison-loading" aria-live="polite">Updating pair-cost evidence…</p>}{comparison?.status === "unavailable" && <p className="notice">{comparison.reason}</p>}{comparison?.status === "available" && comparison.calibration && comparison.model && comparison.current_order && comparison.alternative_order && <section className="pair-cost-analysis"><div className="pair-cost-heading"><p>Existing Pair: Cost Comparison</p><span>These figures compare the current first two vendors in both orders. Priority settings do not change these cost estimates.</span></div>
        <div className="comparison-grid"><article><span>Current</span><strong>{comparison.current_order.map(vendor => vendor.vendor_name).join(" → ")}</strong><small>{comparison.current_order.map(vendor => vendor.vendor_id).join(" → ")}</small></article><article className="proposed"><span>Proposed</span><strong>{comparison.alternative_order.map(vendor => vendor.vendor_name).join(" → ")}</strong><small>{comparison.alternative_order.map(vendor => vendor.vendor_id).join(" → ")}</small></article><article><span>Comparable calibration entrants</span><strong>{formatNumber(comparison.calibration.sample_size)}</strong><small>Grouped by customer, region, and week.</small></article></div>
        <div className="per-thousand"><article><span>Current pair cost / 1,000</span><strong>{money(comparison.model.current_order_cost_per_1000_eligible_entrants_usd, 2)}</strong></article><article><span>Proposed pair cost / 1,000</span><strong>{money(comparison.model.alternative_order_cost_per_1000_eligible_entrants_usd, 2)}</strong></article><article><span>Pair-cost reduction</span><strong>{comparison.model.pair_cost_reduction_pct == null ? "Not available" : `${formatNumber(comparison.model.pair_cost_reduction_pct)}%`}</strong><small>Compared pair cost only.</small></article></div>
        {comparison.weekly_consistency && <details className="advanced"><summary>Weekly consistency</summary><p>{comparison.weekly_consistency.statement} This is descriptive consistency, not statistical confidence.</p><div className="weekly-list">{comparison.weekly_consistency.weeks.map(week => <span key={week.week_starting}>{week.week_starting}<b>{outcome(String(Number(week.savings_per_entrant_usd) * 1000))} / 1,000</b></span>)}</div></details>}
        {comparison.price_threshold && <section className="price-threshold"><h3>{comparison.price_threshold.headline}</h3><p><HighlightNumbers text={comparison.price_threshold.detail} /></p><small>{comparison.price_threshold.assumption}</small></section>}
      </section>}{comparison?.status === "available" && <button className="continue-button" onClick={() => setActiveArea("business")}>Evaluate Pair-Swap Business Case <span>→</span></button>}</section>
      <section className="step-card pilot business-section" hidden={activeArea !== "business"}><div className="business-header"><div className="business-title"><p>Scenario planning</p><h2>Business case</h2></div><div className="volume-mode" aria-label="Scenario basis"><button className={volumeMode === "recorded" ? "mode-active" : ""} onClick={() => setMode("recorded")}>Recorded period</button><button className={volumeMode === "custom" ? "mode-active" : ""} onClick={() => setMode("custom")}>Custom scenario</button></div></div>{pilot?.recorded_period && volumeMode === "recorded" && <p className="recorded-volume"><b>{formatNumber(pilot.recorded_period.eligible_verification_volume)} recorded first-registry checks</b> · {pilot.recorded_period.scope.from} to {pilot.recorded_period.scope.to}</p>}<div className="pilot-inputs"><label>{volumeMode === "recorded" ? "Recorded eligible volume" : "Custom eligible volume"}<input type="number" disabled={volumeMode === "recorded"} min="0" value={volume} onChange={event => setVolume(event.target.value)} /><small>{volumeMode === "custom" ? "Scenario only; not annualized or inferred." : "Pooled calibration rate applied to recorded-period volume."}</small></label><label>One-time implementation cost<input type="number" min="0" value={implementationCost} onChange={event => setImplementationCost(event.target.value)} /></label></div>{pilot && <><section className="pilot-results"><article><span>Savings / 1,000</span><strong>{outcome(pilot.vendor_savings_per_1000_eligible_entrants_usd)}</strong></article><article><span>Potential contribution benefit</span><strong>{outcome(pilot.projected_vendor_savings_usd)}</strong></article><article><span>After implementation</span><strong>{outcome(pilot.savings_after_implementation_usd)}</strong></article><article><span>Break-even entrants</span><strong>{Number(implementationCost) === 0 ? "No implementation cost entered" : pilot.break_even_eligible_volume == null ? "Not available" : formatNumber(pilot.break_even_eligible_volume)}</strong></article></section><p className="beneficiary">{pilot.beneficiary_label}</p></>}<button className="export-button" disabled={loading || comparison?.status !== "available"} onClick={exportProposal}>Export proposal</button>{exports && <p className="export-links"><a href={`${API}${exports.json_export_url}`} target="_blank" rel="noreferrer">Open JSON</a><a href={`${API}${exports.html_export_url}`} target="_blank" rel="noreferrer">Open printable proposal</a></p>}</section>
    </>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<App />);
