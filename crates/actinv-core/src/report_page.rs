//! The report page: one self-contained HTML file that reads a result for a person. It shows the
//! problem in sentences, the headline numbers at round cooling times, charts of how they fall,
//! what dominates, the photon spectrum and the caveats, and prints cleanly to PDF. The app, the
//! browser and `actinv report --format html` all write it, from [`crate::digest`]. The charts
//! are inline SVG, so the same functions also write chart files.
use crate::digest::{self, readable_time, HEADLINES};
use serde_json::Value;
use std::fmt::Write;

/// A table the caller adds to the page, such as a waste classification it has evaluated.
pub struct Section {
    pub title: String,
    /// A sentence above the table.
    pub note: String,
    pub header: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

/// What the page shows besides the result itself.
#[derive(Default)]
pub struct PageOptions<'a> {
    /// The problem that produced the result, when the caller has it; read into sentences.
    pub problem: Option<&'a Value>,
    /// The result's file name or label, shown under the title.
    pub label: Option<String>,
    /// When the page was made, as the caller wants it written.
    pub generated: Option<String>,
    pub sections: Vec<Section>,
}

const ACCENT: &str = "#1800ad";

/// Escape text for HTML element content and attribute values.
pub fn esc(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn number_of(value: &Value) -> f64 {
    value.as_f64().unwrap_or(0.)
}

fn steps(result: &Value) -> &[Value] {
    result["steps"].as_array().map_or(&[][..], Vec::as_slice)
}

/// One curve of a time-history chart.
struct Series {
    name: String,
    points: Vec<(f64, f64)>,
}

/// The time axis of a history: time after shutdown on a logarithmic axis when the result cools,
/// else time from the start of the schedule.
fn time_axis(steps: &[Value]) -> (Vec<usize>, f64, bool) {
    if let Some(shut) = digest::shutdown_step(steps) {
        let t0 = number_of(&steps[shut]["t_s"]);
        let after: Vec<usize> = (shut + 1..steps.len())
            .filter(|i| number_of(&steps[*i]["t_s"]) > t0)
            .collect();
        if after.len() >= 2 {
            return (after, t0, true);
        }
    }
    ((0..steps.len()).collect(), 0., false)
}

/// The history of one headline quantity, or `None` when no step carries a positive value.
fn history(result: &Value, headline: usize) -> Option<(Series, bool)> {
    let steps = steps(result);
    let (indices, t0, after_shutdown) = time_axis(steps);
    let value = HEADLINES[headline].value;
    let points: Vec<(f64, f64)> = indices
        .iter()
        .filter_map(|i| {
            let v = value(&steps[*i])?;
            (v > 0. && v.is_finite()).then(|| (number_of(&steps[*i]["t_s"]) - t0, v))
        })
        .collect();
    let (low, high) = points
        .iter()
        .fold((f64::MAX, f64::MIN), |(a, b), p| (a.min(p.1), b.max(p.1)));
    // a quantity that does not change as the material cools (dpa after shutdown) has no history
    // worth drawing
    (points.len() >= 2 && high > 1.01 * low).then(|| {
        (
            Series {
                name: HEADLINES[headline].title.to_owned(),
                points,
            },
            after_shutdown,
        )
    })
}

const MINUTE: f64 = 60.;
const HOUR: f64 = 3600.;
const DAY: f64 = 86400.;
const YEAR: f64 = 365.25 * DAY;

/// Round times a reader knows, for the ticks of a time axis.
const TIME_TICKS: [f64; 24] = [
    1.,
    10.,
    30.,
    MINUTE,
    2. * MINUTE,
    5. * MINUTE,
    10. * MINUTE,
    20. * MINUTE,
    30. * MINUTE,
    HOUR,
    2. * HOUR,
    6. * HOUR,
    12. * HOUR,
    DAY,
    2. * DAY,
    7. * DAY,
    30.4375 * DAY,
    91.3125 * DAY,
    YEAR,
    3. * YEAR,
    10. * YEAR,
    100. * YEAR,
    1e3 * YEAR,
    1e4 * YEAR,
];

/// Keep at most `count` ticks, spread evenly through the list.
fn thin(ticks: Vec<f64>, count: usize) -> Vec<f64> {
    if ticks.len() <= count {
        return ticks;
    }
    let stride = ticks.len().div_ceil(count);
    ticks.into_iter().step_by(stride).collect()
}

fn decade_label(exponent: i32) -> String {
    match exponent {
        -2..=3 => crate::digest::number(10f64.powi(exponent)),
        _ => format!("1e{exponent}"),
    }
}

/// A line chart of one quantity against time, log-scaled on y, and on x when `log_x`. Returns
/// a standalone SVG element.
fn history_svg(series: &Series, unit: &str, log_x: bool) -> String {
    const W: f64 = 640.;
    const H: f64 = 280.;
    const LEFT: f64 = 64.;
    const RIGHT: f64 = 16.;
    const TOP: f64 = 16.;
    const BOTTOM: f64 = 44.;
    let xs: Vec<f64> = series.points.iter().map(|p| p.0).collect();
    let ys: Vec<f64> = series.points.iter().map(|p| p.1.log10()).collect();
    let fx = |x: f64| if log_x { x.max(1e-9).log10() } else { x };
    let (mut x0, mut x1) = xs
        .iter()
        .map(|x| fx(*x))
        .fold((f64::MAX, f64::MIN), |(a, b), x| (a.min(x), b.max(x)));
    if x1 <= x0 {
        x1 = x0 + 1.;
    }
    if !log_x {
        x0 = x0.min(0.);
    }
    let y0 = ys.iter().cloned().fold(f64::MAX, f64::min).floor();
    let mut y1 = ys.iter().cloned().fold(f64::MIN, f64::max).ceil();
    if y1 <= y0 {
        y1 = y0 + 1.;
    }
    let px = |x: f64| LEFT + (fx(x) - x0) / (x1 - x0) * (W - LEFT - RIGHT);
    let py = |y: f64| TOP + (1. - (y - y0) / (y1 - y0)) * (H - TOP - BOTTOM);
    let mut svg = String::new();
    let _ = write!(
        svg,
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui, sans-serif" font-size="11" role="img" aria-label="{} against time">"#,
        esc(&series.name)
    );
    let _ = write!(svg, r#"<rect width="{W}" height="{H}" fill="white"/>"#);
    let decades = (y1 - y0) as i32;
    let step = (decades / 6).max(1);
    let mut e = y0 as i32;
    while e <= y1 as i32 {
        // a short span also marks 2 and 5 within each decade, so the axis can be read
        let multiples: &[f64] = if decades <= 2 { &[1., 2., 5.] } else { &[1.] };
        for m in multiples {
            let v = (e as f64) + m.log10();
            if v > y1 + 1e-9 {
                break;
            }
            let y = py(v);
            let label = if *m == 1. {
                decade_label(e)
            } else if (-2..=3).contains(&e) {
                crate::digest::number(m * 10f64.powi(e))
            } else {
                format!("{m}e{e}")
            };
            let _ = write!(
                svg,
                r#"<line x1="{LEFT}" x2="{}" y1="{y:.1}" y2="{y:.1}" stroke="rgb(227,229,238)"/><text x="{}" y="{:.1}" text-anchor="end" fill="rgb(85,85,85)">{label}</text>"#,
                W - RIGHT,
                LEFT - 6.,
                y + 4.
            );
        }
        e += step;
    }
    let ticks: Vec<f64> = if log_x {
        thin(
            TIME_TICKS
                .iter()
                .copied()
                .filter(|t| (x0 - 1e-9..=x1 + 1e-9).contains(&t.log10()))
                .collect(),
            7,
        )
    } else {
        let span = x1 - x0;
        let raw = span / 5.;
        let unit = TIME_TICKS
            .iter()
            .copied()
            .find(|t| *t >= raw)
            .unwrap_or(raw);
        (0..=((span / unit) as usize))
            .map(|k| x0 + k as f64 * unit)
            .collect()
    };
    for t in ticks {
        let x = px(t);
        let label = if t == 0. {
            "0".into()
        } else {
            readable_time(t)
        };
        let _ = write!(
            svg,
            r#"<line x1="{x:.1}" x2="{x:.1}" y1="{TOP}" y2="{}" stroke="rgb(238,240,245)"/><text x="{x:.1}" y="{}" text-anchor="middle" fill="rgb(85,85,85)">{label}</text>"#,
            H - BOTTOM,
            H - BOTTOM + 15.
        );
    }
    let path: Vec<String> = series
        .points
        .iter()
        .enumerate()
        .map(|(i, (x, y))| {
            format!(
                "{}{:.1},{:.1}",
                if i == 0 { "M" } else { "L" },
                px(*x),
                py(y.log10())
            )
        })
        .collect();
    let _ = write!(
        svg,
        r#"<path d="{}" fill="none" stroke="{ACCENT}" stroke-width="2"/>"#,
        path.join(" ")
    );
    let axis = if log_x {
        "Time after shutdown"
    } else {
        "Time from the start"
    };
    let _ = write!(
        svg,
        r#"<line x1="{LEFT}" x2="{LEFT}" y1="{TOP}" y2="{}" stroke="rgb(153,153,153)"/><line x1="{LEFT}" x2="{}" y1="{}" y2="{}" stroke="rgb(153,153,153)"/><text x="{}" y="{}" text-anchor="middle" fill="rgb(51,51,51)">{axis}</text><text x="14" y="{}" text-anchor="middle" fill="rgb(51,51,51)" transform="rotate(-90 14 {})">{} ({unit})</text></svg>"#,
        H - BOTTOM,
        W - RIGHT,
        H - BOTTOM,
        H - BOTTOM,
        LEFT + (W - LEFT - RIGHT) / 2.,
        H - 6.,
        TOP + (H - TOP - BOTTOM) / 2.,
        TOP + (H - TOP - BOTTOM) / 2.,
        esc(&series.name)
    );
    svg
}

/// The decay-photon spectrum of a step as an SVG bar chart (photons/s/g per group against
/// energy), or `None` when the step has no photon groups.
pub fn photon_svg(step: &Value) -> Option<String> {
    let groups = step["photon_source"]["groups"].as_array()?;
    let bars: Vec<(f64, f64, f64)> = groups
        .iter()
        .map(|g| {
            (
                number_of(&g["low_eV"]) / 1e6,
                number_of(&g["high_eV"]) / 1e6,
                number_of(&g["photons_s_g"]),
            )
        })
        .filter(|b| b.1 > b.0)
        .collect();
    let top = bars.iter().map(|b| b.2).fold(0., f64::max);
    if top <= 0. {
        return None;
    }
    // the axis ends at the highest group that emits, so empty high groups do not squash the rest
    let emax = bars
        .iter()
        .filter(|b| b.2 > 0.)
        .map(|b| b.1)
        .fold(0., f64::max)
        .ceil()
        .max(1.);
    const W: f64 = 640.;
    const H: f64 = 240.;
    const LEFT: f64 = 64.;
    const RIGHT: f64 = 16.;
    const TOP: f64 = 12.;
    const BOTTOM: f64 = 40.;
    let px = |e: f64| LEFT + e.min(emax) / emax * (W - LEFT - RIGHT);
    let py = |v: f64| TOP + (1. - v / top) * (H - TOP - BOTTOM);
    let mut svg = String::new();
    let _ = write!(
        svg,
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="system-ui, sans-serif" font-size="11" role="img" aria-label="Decay photon spectrum"><rect width="{W}" height="{H}" fill="white"/>"#
    );
    for (low, high, value) in &bars {
        if *value <= 0. || *low >= emax {
            continue;
        }
        let (x, w) = (px(*low), (px(*high) - px(*low)).max(1.));
        let y = py(*value);
        let _ = write!(
            svg,
            r#"<rect x="{x:.1}" y="{y:.1}" width="{w:.1}" height="{:.1}" fill="{ACCENT}" fill-opacity="0.75"/>"#,
            H - BOTTOM - y
        );
    }
    let step_mev = if emax > 6. { 2. } else { 1. };
    let mut e = 0.;
    while e <= emax + 1e-9 {
        let _ = write!(
            svg,
            r#"<text x="{:.1}" y="{}" text-anchor="middle" fill="rgb(85,85,85)">{}</text>"#,
            px(e),
            H - BOTTOM + 15.,
            digest::number(e)
        );
        e += step_mev;
    }
    let _ = write!(
        svg,
        r#"<text x="{:.1}" y="{:.1}" text-anchor="end" fill="rgb(85,85,85)">{}</text><text x="{}" y="{}" text-anchor="end" fill="rgb(85,85,85)">0</text><line x1="{LEFT}" x2="{}" y1="{}" y2="{}" stroke="rgb(153,153,153)"/><text x="{}" y="{}" text-anchor="middle" fill="rgb(51,51,51)">Photon energy (MeV)</text><text x="14" y="{}" text-anchor="middle" fill="rgb(51,51,51)" transform="rotate(-90 14 {})">photons/s/g per group</text></svg>"#,
        LEFT - 6.,
        TOP + 8.,
        digest::sci(top),
        LEFT - 6.,
        H - BOTTOM,
        W - RIGHT,
        H - BOTTOM,
        H - BOTTOM,
        LEFT + (W - LEFT - RIGHT) / 2.,
        H - 6.,
        TOP + (H - TOP - BOTTOM) / 2.,
        TOP + (H - TOP - BOTTOM) / 2.
    );
    Some(svg)
}

/// The charts a result supports, as (file stem, title, SVG): one history per headline quantity
/// that has one, and the photon spectrum at shutdown.
pub fn charts(result: &Value) -> Vec<(String, String, String)> {
    let mut out = Vec::new();
    for (i, headline) in HEADLINES.iter().enumerate() {
        if let Some((series, log_x)) = history(result, i) {
            // `Contact dose (screening)` becomes `contact-dose`
            let stem = headline
                .title
                .split(" (")
                .next()
                .unwrap_or("chart")
                .to_lowercase()
                .replace(' ', "-");
            out.push((
                stem,
                format!("{} as the material cools", headline.title),
                history_svg(&series, headline.unit, log_x),
            ));
        }
    }
    let steps = steps(result);
    if let Some(step) = digest::shutdown_step(steps).and_then(|i| steps.get(i)) {
        if let Some(svg) = photon_svg(step) {
            out.push((
                "photons".into(),
                "Decay photon spectrum at shutdown".into(),
                svg,
            ));
        }
    }
    out
}

fn dominant_html(title: &str, entries: &[(String, f64)]) -> String {
    let mut html = format!(r#"<div class="dominant"><h3>{}</h3><table>"#, esc(title));
    for (nuclide, percent) in entries {
        let share = if *percent < 0.05 {
            "&lt;0.1%".to_owned()
        } else {
            format!("{percent:.1}%")
        };
        let _ = write!(
            html,
            r#"<tr><td class="nuclide">{}</td><td class="bar"><span style="width:{:.1}%"></span></td><td class="share">{share}</td></tr>"#,
            esc(nuclide),
            percent.clamp(0., 100.)
        );
    }
    html.push_str("</table></div>");
    html
}

fn table_html(header: &[String], rows: &[Vec<String>]) -> String {
    let mut html = String::from("<table class=\"grid\"><thead><tr>");
    for h in header {
        let _ = write!(html, "<th>{}</th>", esc(h));
    }
    html.push_str("</tr></thead><tbody>");
    for row in rows {
        html.push_str("<tr>");
        for cell in row {
            let _ = write!(html, "<td>{}</td>", esc(cell));
        }
        html.push_str("</tr>");
    }
    html.push_str("</tbody></table>");
    html
}

fn file_name(path: &Value) -> String {
    let text = path["path"].as_str().or(path.as_str()).unwrap_or("");
    text.rsplit(['/', '\\']).next().unwrap_or(text).to_owned()
}

const STYLE: &str = r#"
:root { --accent: #1800ad; --muted: #5b6070; --line: #e3e5ee; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f7fb; color: #1d1f29; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Ubuntu, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 20px 64px; background: white; min-height: 100vh; }
header { border-bottom: 3px solid var(--accent); padding-bottom: 12px; margin-bottom: 8px; }
.brand { color: var(--accent); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
h1 { font-size: 26px; font-weight: 500; margin: 4px 0; }
.meta { color: var(--muted); font-size: 13px; }
h2 { font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); margin: 32px 0 8px; }
h3 { font-size: 14px; margin: 0 0 6px; }
dl.problem { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0; }
dl.problem dt { color: var(--muted); }
dl.problem dd { margin: 0; }
table.grid { border-collapse: collapse; width: 100%; font-size: 14px; }
table.grid th, table.grid td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); }
table.grid th { font-weight: 600; color: var(--muted); font-size: 12px; }
table.grid td.num, table.grid th.num { text-align: right; font-variant-numeric: tabular-nums; }
.charts figure { margin: 16px 0; }
.charts svg { width: 100%; height: auto; border: 1px solid var(--line); }
figcaption { color: var(--muted); font-size: 13px; margin-bottom: 4px; }
.dominants { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.dominant table { width: 100%; border-collapse: collapse; font-size: 14px; }
.dominant td { padding: 3px 0; }
.dominant td.nuclide { width: 72px; }
.dominant td.share { width: 56px; text-align: right; font-variant-numeric: tabular-nums; }
.dominant td.bar span { display: block; height: 8px; background: var(--accent); border-radius: 2px; min-width: 2px; }
ul.caveats { padding-left: 20px; margin: 0; }
.note { color: var(--muted); font-size: 13px; }
footer { margin-top: 40px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); padding-top: 8px; }
@media (max-width: 640px) { .dominants { grid-template-columns: 1fr; } main { padding: 20px 16px 48px; } }
@media print { body { background: white; } main { padding: 0; max-width: none; } h2 { break-after: avoid; } figure, table { break-inside: avoid; } }
"#;

/// The report page of a result. `Err` when the result has no steps.
pub fn html(result: &Value, options: &PageOptions) -> Result<String, String> {
    let steps = steps(result);
    if steps.is_empty() {
        return Err("the result has no steps".into());
    }
    let title = result["spec_title"].as_str().unwrap_or("ACTINV result");
    let mut body = String::new();
    let mut meta = vec![format!("ACTINV {}", env!("CARGO_PKG_VERSION"))];
    if let Some(label) = &options.label {
        meta.push(label.clone());
    }
    if let Some(generated) = &options.generated {
        meta.push(generated.clone());
    }
    let _ = write!(
        body,
        r#"<header><div class="brand">Activation and inventory report</div><h1>{}</h1><div class="meta">{}</div></header>"#,
        esc(title),
        esc(&meta.join(" · "))
    );

    if let Some(problem) = options.problem {
        body.push_str("<h2>The problem</h2><dl class=\"problem\">");
        for (name, line) in [
            ("Material", digest::material(problem)),
            ("Irradiation and cooling", digest::schedule(problem)),
            ("Source", digest::source(problem)),
            ("Computed", digest::outputs(problem)),
        ] {
            let text = line.unwrap_or_else(|missing| missing);
            let _ = write!(body, "<dt>{name}</dt><dd>{}</dd>", esc(&text));
        }
        body.push_str("</dl>");
    }

    // the headline numbers at round cooling times
    let marks = digest::landmarks(steps);
    let marks = if marks.is_empty() {
        vec![("Last step".to_owned(), steps.len() - 1)]
    } else {
        marks
    };
    let shown: Vec<usize> = (0..HEADLINES.len())
        .filter(|h| steps.iter().any(|s| (HEADLINES[*h].value)(s).is_some()))
        .collect();
    body.push_str("<h2>At a glance</h2><table class=\"grid\"><thead><tr><th>When</th>");
    for h in &shown {
        let _ = write!(
            body,
            r#"<th class="num">{} ({})</th>"#,
            esc(HEADLINES[*h].title),
            HEADLINES[*h].unit
        );
    }
    body.push_str("</tr></thead><tbody>");
    for (label, index) in &marks {
        let when = digest::when(steps, *index);
        let label = if label == "At shutdown" || when == "at shutdown" {
            label.clone()
        } else {
            format!("{label} ({when})")
        };
        let _ = write!(body, "<tr><td>{}</td>", esc(&label));
        for h in &shown {
            let text = (HEADLINES[*h].value)(&steps[*index])
                .map(digest::sci)
                .unwrap_or_else(|| "–".into());
            let _ = write!(body, r#"<td class="num">{text}</td>"#);
        }
        body.push_str("</tr>");
    }
    body.push_str("</tbody></table>");
    let missing: Vec<String> = HEADLINES
        .iter()
        .enumerate()
        .filter(|(i, _)| !shown.contains(i))
        .map(|(_, h)| format!("{}: {}", h.title, digest::absent(h, &steps[0])))
        .collect();
    if !missing.is_empty() {
        let _ = write!(body, r#"<p class="note">{}</p>"#, esc(&missing.join(" ")));
    }
    if shown
        .iter()
        .any(|h| HEADLINES[*h].title.starts_with("Contact dose"))
    {
        body.push_str(r#"<p class="note">The contact dose is a screening estimate: the gamma dose rate in air at the surface of a large slab of the material.</p>"#);
    }

    let charts = charts(result);
    if !charts.is_empty() {
        body.push_str("<h2>How it changes</h2><div class=\"charts\">");
        for (_, caption, svg) in &charts {
            let _ = write!(
                body,
                "<figure><figcaption>{}</figcaption>{svg}</figure>",
                esc(caption)
            );
        }
        body.push_str("</div>");
    }

    let shut = digest::shutdown_step(steps).unwrap_or(steps.len() - 1);
    let activity = digest::top_activity(&steps[shut], 8);
    if !activity.is_empty() {
        let _ = write!(
            body,
            "<h2>What dominates, {}</h2><div class=\"dominants\">{}",
            esc(&digest::when(steps, shut)),
            dominant_html("Activity", &activity)
        );
        if let Some((caption, heat)) = digest::top_heat(&steps[shut], 8) {
            body.push_str(&dominant_html(caption, &heat));
        }
        body.push_str("</div>");
    }

    for section in &options.sections {
        let _ = write!(body, "<h2>{}</h2>", esc(&section.title));
        if !section.note.is_empty() {
            let _ = write!(body, "<p>{}</p>", esc(&section.note));
        }
        body.push_str(&table_html(&section.header, &section.rows));
    }

    let caveats = digest::caveats(result);
    body.push_str("<h2>Caveats</h2>");
    if caveats.is_empty() {
        body.push_str("<p>The result's ledger records nothing a reader needs to weigh.</p>");
    } else {
        body.push_str("<ul class=\"caveats\">");
        for note in &caveats {
            let _ = write!(body, "<li>{}</li>", esc(note));
        }
        body.push_str("</ul>");
    }

    let certificate = &result["certificate"];
    let data: Vec<String> = [
        ("Cross sections", &certificate["library"]),
        ("Decay data", &certificate["decay_primary"]),
        ("Decay fallback", &certificate["decay_fallback"]),
    ]
    .iter()
    .filter_map(|(name, value)| {
        let file = file_name(value);
        (!file.is_empty()).then(|| format!("{name}: {file}"))
    })
    .collect();
    if !data.is_empty() {
        let _ = write!(
            body,
            r#"<h2>Data</h2><p class="note">{}</p>"#,
            esc(&data.join(" · "))
        );
    }
    let _ = write!(
        body,
        "<footer>Made with ACTINV {} from a result with {} steps. The complete result, with every step, the ledger and the certificate, is in its JSON file.</footer>",
        env!("CARGO_PKG_VERSION"),
        steps.len()
    );
    Ok(format!(
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{} · ACTINV report</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>\n",
        esc(title)
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn result() -> Value {
        json!({
            "spec_title": "Iron <test>",
            "pathway_closure": 0.0,
            "ledger": {"decay_nuclides_from_fallback": 0},
            "certificate": {"library": {"path": "/data/tendl-709.bin"}},
            "steps": [
                {"t_s": 300.0, "flux": 1.0, "activity_Bq_per_g": {"Mn56": 3.0e5, "Fe53": 1.0e5},
                 "heat_W_per_g": {"total": 1e-7},
                 "photon_source": {"groups": [
                     {"low_eV": 0.0, "high_eV": 1.0e6, "photons_s_g": 2.0e5},
                     {"low_eV": 1.0e6, "high_eV": 2.0e6, "photons_s_g": 5.0e4},
                     {"low_eV": 1.0e7, "high_eV": 2.0e7, "photons_s_g": 0.0}]}},
                {"t_s": 3900.0, "flux": 0.0, "activity_Bq_per_g": {"Mn56": 1.0e5},
                 "heat_W_per_g": {"total": 5e-8}},
                {"t_s": 86700.0, "flux": 0.0, "activity_Bq_per_g": {"Mn56": 1.0e3},
                 "heat_W_per_g": {"total": 1e-9}},
            ]
        })
    }

    #[test]
    fn the_page_reads_the_result_and_escapes_its_text() {
        let page = html(&result(), &PageOptions::default()).unwrap();
        assert!(page.starts_with("<!doctype html>"));
        assert!(page.contains("Iron &lt;test&gt;"));
        assert!(!page.contains("Iron <test>"));
        assert!(page.contains("At shutdown"));
        assert!(page.contains("~1 h (1 h after shutdown)"));
        assert!(page.contains("4.00e5"), "total activity at shutdown");
        assert!(page.contains("Mn56"));
        assert!(page.contains("tendl-709.bin"));
        assert!(page.contains("Damage: Not computed"));
        assert!(page.contains("<svg"));
    }

    #[test]
    fn the_problem_reads_as_sentences_when_given() {
        let problem = json!({"material": {"basis": "wt_percent", "mass_g": 1.0,
            "composition": {"FE": 100.0}}});
        let page = html(
            &result(),
            &PageOptions {
                problem: Some(&problem),
                ..Default::default()
            },
        )
        .unwrap();
        assert!(page.contains("<dd>Fe 100 wt% · 1 g</dd>"));
    }

    #[test]
    fn charts_cover_each_history_and_the_photon_spectrum() {
        let names: Vec<String> = charts(&result()).into_iter().map(|c| c.0).collect();
        assert_eq!(names, ["activity", "decay-heat", "photons"]);
        let svg = photon_svg(&result()["steps"][0]).unwrap();
        assert!(
            svg.contains(">2<"),
            "the energy axis ends at the last emitting group"
        );
        assert!(!svg.contains(">20<"));
    }

    #[test]
    fn a_result_without_steps_has_no_page() {
        assert!(html(&json!({"steps": []}), &PageOptions::default()).is_err());
    }
}
