# Interface direction contract

This contract is the release gate for the merchant review interface. It turns the product brief into observable UI decisions so visual changes can be reviewed without relying on taste alone.

## THESIS

The interface is an evidence-led decision ledger that separates deterministic business facts from generated judgement. A merchant operator should be guided to define the decision first, then verify scope, target, outcome, path, experiment uncertainty and cost before accepting any model-generated explanation.

## OWN-WORLD

The product belongs to the world of merchant operating review and audit, not generic analytics dashboards or chat assistants. Language names business objects directly: activity period, comparison period, completed orders, merchant discount, order contribution, evidence and validation condition.

## STORY

Every complete review follows one stable sequence:

1. State which operating decision the review must support and the primary risk to examine.
2. Confirm campaign, periods and attribution scope.
3. Establish target attainment and the operating trade-off.
4. Inspect the customer path and variant evidence.
5. Assess experimental confidence and cost/value.
6. Separate confirmed facts, explanatory hypotheses and open questions.
7. Persist one testable next action, its metric, guardrail and stop/continue condition as a decision record.

## FIRST VIEWPORT

On desktop, the first viewport must show the active record, the result tension, target state, order-contribution movement, evidence identity, the beginning of the customer path and the Agent judgement pane. On narrow screens, the full confirmed date range remains visible and explicit tabs switch between deterministic results and Agent judgement.

## FORM

Code-led seed: **split audit sheet**. The persistent record rail represents review history; the central ruled dossier carries evidence; the bounded right pane carries interpretation. Cobalt is reserved for focus, selection and primary action. Rules and tonal surfaces create hierarchy instead of stacks of floating cards, decorative gradients or oversized marketing copy.

## QUALITY BAR

A release is rejected if any of these conditions is true:

- Synthetic data is not visibly labelled from backend provenance.
- A metric cannot be traced to an evidence ID and confirmed scope.
- Model failure loops back into the same known configuration error.
- A narrow viewport clips the campaign or date range.
- Print/export omits either deterministic evidence or the final Agent judgement.
- Explanatory prose is smaller than 16px in the interactive interface.
- The interface presents correlation as causation, financial ROI without full costs, or generated outcomes as observed facts.
