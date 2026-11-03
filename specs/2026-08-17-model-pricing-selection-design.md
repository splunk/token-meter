# Explicit Model Pricing Selection Design

## Goal

Make it unambiguous which model prices will change when a user chooses
**From now**, **From date**, or **All history**, without returning to repeated
per-row Save buttons or retaining a mostly empty Actions column.

## Interaction Model

Each model row begins with an unchecked selection checkbox. A selected row is
visibly highlighted and is the only kind of row included in **Save models**.
Editing any price automatically selects its row. A user can also select a row
before editing it.

Unchecking a selected row excludes it from the next save and restores that
row's inputs to their currently saved values. This prevents an edited but
unselected draft from looking as though it might still be applied.

The effective scope is labelled **Apply to selected models** and retains the
three existing choices:

- **From now** starts a new price period when the save completes.
- **From date** requires a valid date and changes only matching sessions at or
  after that time.
- **All history** replaces the selected models' complete price timelines and
  retains the existing warning and confirmation.

No unchecked model is sent to the server. Multiple selected rows are saved in
one atomic request with the same effective scope.

## Table and Actions

The table columns become:

1. Select
2. Provider
3. Model ID
4. Input
5. Output
6. Cache write
7. Cached input

The existing Actions column is removed.

Relevant lifecycle actions move beneath the model ID:

- a bundled model that is overridden, or still has saved price periods, shows
  **Restore built-in price**;
- a custom model shows **Remove custom model**;
- an ordinary bundled model shows no lifecycle action.

These contextual actions keep their existing effective-scope semantics and
confirmation. Moving them beside the affected model preserves functionality
without making every row reserve an action column.

## Selection Feedback

The section always states the selection count:

- **No models selected** when idle;
- **1 model selected**;
- **3 models selected**.

The primary button follows the same state:

- **Save models** while disabled with no selection;
- **Save 1 model**;
- **Save 3 models**.

After a successful save, the selection clears, the rows return to their idle
appearance, and the existing scope-specific success message remains visible.
If saving fails, selection and entered values remain so the user can correct
the problem or retry.

## Desktop and Accessible Behavior

The pricing surface retains its table layout throughout the supported desktop
range. At the 1024-pixel laptop target, every selection control, model ID,
provider, price field, and contextual lifecycle action remains visible without
page-level horizontal overflow.

Each checkbox has a model-specific accessible name such as **Select gpt-5.6
for price update**. Selection is communicated by checkbox state, text count,
and row styling rather than color alone. Keyboard users can reach every
checkbox, price input, contextual lifecycle action, scope control, and Save
button in a predictable order.

## State and Data Flow

The browser owns a bounded set of selected provider/model keys. Input events
add the owning key; checkbox changes add or remove it. Rendering preserves
selected drafts during live polling. Save constructs the existing bounded
`changes` array from selected keys only, so the server's atomic batch endpoint
and price-history engine require no contract change.

## Error Handling

- Empty, negative, non-numeric, or oversized prices block the complete batch
  before any request is sent.
- **From date** without a valid past or current date blocks saving with the
  existing actionable validation.
- A stale or missing selected row asks the user to reload Settings.
- Network and server errors preserve every selected draft.
- Contextual restore/remove failures preserve the current rendered row and
  report the existing recovery message.

## Verification

Automated dashboard contracts will prove that selection checkboxes replace the
Actions column, editing selects a row, unchecking restores saved values, save
labels pluralize from the selected count, and only selected rows form the
batch. Existing pricing, full dashboard, JavaScript parsing, and backend batch
tests must remain green.

Browser QA will cover selection, exclusion, date scope, save enablement,
contextual restore/remove placement, keyboard focus, and zero horizontal
overflow at wide-desktop and 1024-pixel-laptop widths. The
exact committed source will then be installed and checked for service health,
native operation, and source/runtime parity.
