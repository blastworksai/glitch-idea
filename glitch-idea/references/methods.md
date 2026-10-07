# Exploration and assessment decisions

## Scope describes the outcome

| Scope | Boundary | Useful evidence |
|---|---|---|
| Small change (`small-change`) | Bounded improvement to an existing capability | The existing behaviour and precise change |
| Capability (`capability`) | One coherent, independently useful ability | Who gains that ability and its acceptance boundary |
| Project (`project`) | Several capabilities together deliver an outcome | Capability boundaries and their dependencies |
| Epic (`epic`) | Several projects serve a broader outcome | Outcome/dependency map and the next decision |

A screen count, repository count or terse description does not determine scope. Record risk, uncertainty and dependencies in labelled `assumptions` entries and explain the classification in `scope_reason`. This is not an effort or delivery-date estimate.

## Four development methods

The operator chooses; the agent never selects or favours one. The id in parentheses is the helper's stored `method` value.

| Choice | Fixed now | Earns the next step | Good fit |
|---|---|---|---|
| Full Plan Up Front (`bounded-plan`) | Clear scope and acceptance criteria | Verification of the bounded change | An understood change |
| Vertical Slicing (Agile) (`adaptive-slices`) | Outcome and next useful increment | Build/use evidence reshapes the next increment | Evolving requirements or larger work |
| Fixed Budget, Build what Fits (`appetite-led`) | Operator's investment cap and outcome boundaries | Completed bet or explicit stop/reshape | Scope can flex inside the chosen cap |
| Experiment First (`experiment-led`) | A question, evidence criterion and bounded experiment | Evidence supports proceeding, changing or stopping | Uncertain value or feasibility |

APIV (Align, Plan, Implement, Verify) is the loop all four methods run inside. These are local operating choices, not claims to implement entire frameworks. A Full Plan Up Front item may deliver a slice or experiment inside another method without erasing its learning loop.

### Inner methods for sketched items

Each item sketched in Exploration may name an inner method, or none (it then follows the overall method). The meaningful pairings:

- Any overall method may run an item as Experiment First.
- Fixed Budget, Build what Fits items may be Vertical Slicing.
- Full Plan Up Front items stay overall: no inner method.

For Fixed Budget work, obtain the operator's cap, unit and boundary in Exploration instead of inventing them. For Experiment First work, ask in Exploration for the question, the evidence, what counts as success and when to stop. For experiments, distinguish customer/value hypotheses from technical feasibility, and label throwaway work versus production-quality exploratory code. Specify the evidence that would stop or redirect work. Method changes require the operator's decision when new evidence invalidates the old assumptions.

## Human ratings and brain assessments

Human urgency and importance are independent integer answers from 1 to 10. Record exactly what the operator supplied; incomplete ideas retain nulls. Never derive them from the brain's numbers.

Choose an assessment with usable inputs. Method choice does not depend on scope tier, and WSJF is not a compulsory default. Every assessment carries a version, unit/cohort basis, assumptions, confidence and provenance. The helper calculates; the agent supplies grounded inputs and labels model estimates. A shared formula alone does not make different cohorts, time horizons or effort units comparable.

| Method | Helper calculation / interpretation | Required caution |
|---|---|---|
| WSJF | `(value + time_criticality + enablement) / effort` | Relative delay-cost inputs and relative effort proxy need a shared scale and cohort. Record proxy assumptions. |
| RICE | `reach * impact * confidence / effort` | Define reach period, impact scale and effort unit; confidence input is a fraction 0–1 (80% → 0.8). |
| Kano | Category plus `hypothesis` flag; no numerical score | An AI needs classification is a hypothesis, not customer research. Cite evidence before claiming otherwise. |

Any missing numerical input produces an unknown score, never zero. A provisional assessment with unknown inputs can support a qualitative placement suggestion when the operator's ratings are supplied; explain the unknowns rather than filling them with invented values. An operator can move an incomplete idea manually. Do not rank unlike raw methods as if their numerical scales were interchangeable. No formula determines the operator's accepted order.

Placement explanation: show the operator's two ratings, the named brain assessment, neighbours, the trade-off, and the strongest condition that would change the suggestion. Save the proposal before presenting it as recorded. An operator may choose differently without explaining why; retain both the original recommendation and the actual decision.

Primary references (checked 25 September 2026): [SAFe WSJF](https://framework.scaledagile.com/wsjf/), [Intercom RICE](https://www.intercom.com/blog/rice-simple-prioritization-for-product-managers/), [ASQ Kano](https://asq.org/quality-resources/kano-model). The formulas above are documented local implementations; Kano's additional stored categories are classifications, not numerical ranking levels.
