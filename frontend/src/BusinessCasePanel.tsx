import { useEffect, useState, type FormEvent } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

type QbrCase = {
  decision: { integration_consistency_objection: string };
  scenario_comparison: {
    retain_existing_setup: { incremental_verifications: string; gross_first_year_value_usd: string; value_after_implementation_budget_usd: string; note: string };
    adopt_alaska_feature: { potential_additional_verifications_at_full_realization: number; incremental_verifications_at_selected_realization: string; gross_first_year_value_usd: string; value_after_implementation_budget_usd: string; implementation_budget_applied_usd: string; note: string };
  };
};

function money(value: string) { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value)); }
function count(value: string) { return Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 }); }

export function BusinessCasePanel() {
  const [choice, setChoice] = useState("retain_existing_setup");
  const [realization, setRealization] = useState("1");
  const [budget, setBudget] = useState("0");
  const [result, setResult] = useState<QbrCase | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const share = Number(realization), enteredBudget = Number(budget);
    if (!Number.isFinite(share) || share < 0 || share > 1 || !Number.isFinite(enteredBudget) || enteredBudget < 0) return;
    const handle = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/business-case/qbr`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ realization_factor: share, implementation_budget_usd: enteredBudget, adoption_choice: choice }) });
        const body = await response.json();
        if (response.ok) { setResult(body); setError(""); } else setError(body.detail ?? "Could not evaluate this feature-adoption case.");
      } catch { setError("Could not evaluate this feature-adoption case."); }
    }, 180);
    return () => window.clearTimeout(handle);
  }, [choice, realization, budget]);
  function noSubmit(event: FormEvent) { event.preventDefault(); }
  const sharePercent = (Number(realization || 0) * 100).toFixed(0);
  const scenarios = result?.scenario_comparison;
  return <section className="feature-case">
    <div className="feature-intro"><p className="eyebrow">Separate decision case</p><h2>Feature adoption · Betting.com Alaska</h2><p>This scenario is separate from all CSV customer calculations. The QBR recorded a preference to retain one integration for consistency; the alternative below models the approved Alaska feature.</p></div>
    <form onSubmit={noSubmit} className="feature-controls"><label>Decision focus<select value={choice} onChange={event => setChoice(event.target.value)}><option value="retain_existing_setup">Retain existing setup (recorded choice)</option><option value="test_feature">Adopt Alaska feature</option></select></label><label>Share of modeled opportunity realized<input type="number" min="0" max="1" step="0.01" value={realization} onChange={event => setRealization(event.target.value)} /><small>{sharePercent}% of the modeled adoption opportunity is used to calculate adoption-only additional verifications and gross first-year value.</small></label><label>Incremental implementation budget<input type="number" min="0" step="1" value={budget} onChange={event => setBudget(event.target.value)} /><small>Applied only to the adoption scenario.</small></label></form>
    {error && <p className="error">{error}</p>}
    {scenarios && <><section className="adoption-scenarios"><article className={choice === "retain_existing_setup" ? "active-scenario" : ""}><p className="eyebrow">Current choice</p><h3>Retain existing setup</h3><strong>0</strong><span>Incremental verifications</span><p>{scenarios.retain_existing_setup.note}</p><small>Gross first-year value: {money(scenarios.retain_existing_setup.gross_first_year_value_usd)} · No incremental budget applied.</small></article><article className={choice === "test_feature" ? "active-scenario" : ""}><p className="eyebrow">Modeled alternative</p><h3>Adopt Alaska feature</h3><strong>{count(scenarios.adopt_alaska_feature.incremental_verifications_at_selected_realization)}</strong><span>Additional verifications at {sharePercent}% realized</span><p>Potential additional verifications at full realization under adoption: <b>{scenarios.adopt_alaska_feature.potential_additional_verifications_at_full_realization.toLocaleString()}</b> (40,000 anticipated signups × 9.5 percentage points).</p><small>Modeled gross first-year value: {money(scenarios.adopt_alaska_feature.gross_first_year_value_usd)} · Value after implementation budget: {money(scenarios.adopt_alaska_feature.value_after_implementation_budget_usd)}. This is gross value minus budget, not profit.</small></article></section><details className="evidence"><summary>Evidence and assumptions</summary><p><b>Integration-consistency objection:</b> {result.decision.integration_consistency_objection}</p><p><b>$300:</b> assumed first-year value per new user, not lifetime value or profit.</p><p>{scenarios.adopt_alaska_feature.note}</p></details></>}
  </section>;
}
