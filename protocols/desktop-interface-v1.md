# Desktop interface verification protocol v1

Scope: the new native desktop delegates calculations to the existing core; it must preserve physical inputs and scientific results. No physics changes are planned.

Frozen checks, before collecting integration evidence:

1. Generate the existing P11 synthetic activation/decay/covariance fixture in a temporary directory. Request inventory, activity, heat, photons, and pathways in trace mode.
2. Run the same specification through the CLI and the desktop's shared worker function. Require exact equality of steps, pathways, ledger, mode, and state counts. Entry-point labels and elapsed times may differ; certificates must retain identical input hashes.
3. Parse and export the desktop result as JSON; require structural equality with its original result. CSV must preserve selected step inventory and activity units.
4. Regression tests must reject malformed result rows and unknown problem fields, preserve advanced inputs, and resolve every supported file reference relative to the explicit input base without mutating the editor document.
5. Render all eight pages and all setup tour steps using the native application. Inspect captured images for clipping, missing targets, readable branding, and visible dimming/highlight/callout layers. Test tour navigation and dismissal through egui input events.
6. Run fmt, workspace check, workspace Clippy, and workspace tests. Record pre-existing failures separately and run the committed tree independently of unrelated untracked files.

Generated nuclear inputs and full result fixtures stay outside version control. Store compact verification outcomes and the protocol SHA-256 in the desktop evidence record.
