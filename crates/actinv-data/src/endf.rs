//! ENDF-6 primitives: checked fixed-width records and the exponent-without-`e` number form.
//!
//! The tuple-returning helpers are retained for compatibility. Production parsers use the checked records below:
//! malformed numeric fields and truncated payloads are errors rather than values silently changed to zero.

use std::collections::HashSet;

pub type ListRecord = (f64, f64, i32, i32, usize, usize, Vec<f64>);
pub type Tab1Record = (f64, f64, i32, i32, Vec<(usize, i32)>, Vec<(f64, f64)>);

/// Parse one ENDF floating-point field. A blank data field is the ENDF zero value.
pub fn parse_endf_float(s: &str) -> Result<f64, String> {
    let text = s.trim();
    if text.is_empty() {
        return Ok(0.0);
    }
    let value = if let Ok(value) = text.parse::<f64>() {
        value
    } else {
        let bytes = text.as_bytes();
        let split = (1..bytes.len()).find(|&index| {
            matches!(bytes[index], b'+' | b'-') && !matches!(bytes[index - 1], b'e' | b'E')
        });
        let index = split.ok_or_else(|| format!("invalid ENDF number '{text}'"))?;
        let normalized_length = text.len() + 1;
        let mut normalized = [0_u8; 12];
        if normalized_length > normalized.len() {
            return Err(format!("invalid ENDF number '{text}'"));
        }
        normalized[..index].copy_from_slice(&bytes[..index]);
        normalized[index] = b'e';
        normalized[index + 1..normalized_length].copy_from_slice(&bytes[index..]);
        std::str::from_utf8(&normalized[..normalized_length])
            .map_err(|_| format!("invalid ENDF number '{text}'"))?
            .parse::<f64>()
            .map_err(|_| format!("invalid ENDF number '{text}'"))?
    };
    if value.is_finite() {
        Ok(value)
    } else {
        Err(format!("nonfinite ENDF number '{text}'"))
    }
}

/// Parse one ENDF integer field. Blank integer fields are zero.
pub fn parse_endf_i32(s: &str) -> Result<i32, String> {
    let text = s.trim();
    if text.is_empty() {
        Ok(0)
    } else {
        text.parse::<i32>()
            .map_err(|_| format!("invalid ENDF integer '{text}'"))
    }
}

pub fn parse_endf_usize(s: &str) -> Result<usize, String> {
    let value = parse_endf_i32(s)?;
    usize::try_from(value).map_err(|_| format!("negative ENDF count {value}"))
}

/// Parse an ENDF numeric field: standard floats plus the exponent-without-`e` form used throughout ENDF.
pub fn endf_float(s: &str) -> f64 {
    parse_endf_float(s).expect("invalid ENDF float in legacy parser")
}

/// The six 11-character data fields of an ENDF record.
pub fn fields(line: &str) -> [&str; 6] {
    let mut out = [""; 6];
    if !line.is_ascii() {
        return out;
    }
    for (i, o) in out.iter_mut().enumerate() {
        let (a, b) = (i * 11, (i + 1) * 11);
        *o = if line.len() >= b {
            &line[a..b]
        } else if line.len() > a {
            &line[a..]
        } else {
            ""
        };
    }
    out
}

/// Checked six-field data area. ENDF records are ASCII; byte slicing a non-ASCII record would be ambiguous.
pub fn checked_fields(line: &str) -> Result<[&str; 6], String> {
    if !line.is_ascii() {
        return Err("non-ASCII ENDF record".into());
    }
    if line.len() < 66 {
        return Err(format!("truncated ENDF record: {} columns", line.len()));
    }
    Ok(std::array::from_fn(|index| {
        &line[index * 11..(index + 1) * 11]
    }))
}

/// (MAT, MF, MT) from the tail of an ENDF record, if present.
pub fn tail(line: &str) -> Option<(i32, i32, i32)> {
    if !line.is_ascii() || line.len() < 75 {
        return None;
    }
    let mat = line[66..70].trim().parse::<i32>().ok()?;
    let mf = line[70..72].trim().parse::<i32>().ok()?;
    let mt = line[72..75].trim().parse::<i32>().ok()?;
    Some((mat, mf, mt))
}

pub fn checked_tail(line: &str) -> Result<(i32, i32, i32), String> {
    if !line.is_ascii() {
        return Err("non-ASCII ENDF record".into());
    }
    if line.len() < 75 {
        return Err(format!("truncated ENDF record: {} columns", line.len()));
    }
    Ok((
        parse_endf_i32(&line[66..70])?,
        parse_endf_i32(&line[70..72])?,
        parse_endf_i32(&line[72..75])?,
    ))
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ContRecord {
    pub c1: f64,
    pub c2: f64,
    pub l1: i32,
    pub l2: i32,
    pub n1: usize,
    pub n2: usize,
}

impl ContRecord {
    pub fn parse(line: &str) -> Result<Self, String> {
        let value = checked_fields(line)?;
        Ok(Self {
            c1: parse_endf_float(value[0])?,
            c2: parse_endf_float(value[1])?,
            l1: parse_endf_i32(value[2])?,
            l2: parse_endf_i32(value[3])?,
            n1: parse_endf_usize(value[4])?,
            n2: parse_endf_usize(value[5])?,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct CheckedListRecord {
    pub head: ContRecord,
    pub values: Vec<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CheckedTab1Record {
    pub head: ContRecord,
    /// One-based `(NBT, INT)` pairs.
    pub interpolation: Vec<(usize, i32)>,
    pub points: Vec<(f64, f64)>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CheckedTab2Record {
    pub head: ContRecord,
    /// One-based `(NBT, INT)` pairs for the following records.
    pub interpolation: Vec<(usize, i32)>,
}

fn line_at<'a>(lines: &'a [&'a str], index: usize, kind: &str) -> Result<&'a str, String> {
    lines
        .get(index)
        .copied()
        .ok_or_else(|| format!("truncated ENDF {kind} at record {}", index + 1))
}

fn require_payload_fields(
    lines: &[&str],
    next: usize,
    fields: usize,
    kind: &str,
) -> Result<(), String> {
    let available = lines.len().saturating_sub(next).saturating_mul(6);
    if fields > available {
        return Err(format!(
            "truncated ENDF {kind}: declares {fields} fields, at most {available} remain"
        ));
    }
    Ok(())
}

pub fn read_cont_checked(lines: &[&str], index: usize) -> Result<(ContRecord, usize), String> {
    let head = ContRecord::parse(line_at(lines, index, "CONT")?)?;
    Ok((head, index + 1))
}

pub fn read_list_checked(
    lines: &[&str],
    index: usize,
) -> Result<(CheckedListRecord, usize), String> {
    let (head, mut next) = read_cont_checked(lines, index)?;
    require_payload_fields(lines, next, head.n1, "LIST payload")?;
    let mut values = Vec::with_capacity(head.n1);
    while values.len() < head.n1 {
        let fields = checked_fields(line_at(lines, next, "LIST payload")?)?;
        for field in fields {
            if values.len() == head.n1 {
                break;
            }
            values.push(parse_endf_float(field)?);
        }
        next += 1;
    }
    Ok((CheckedListRecord { head, values }, next))
}

pub fn read_tab1_checked(
    lines: &[&str],
    index: usize,
) -> Result<(CheckedTab1Record, usize), String> {
    let (head, mut next) = read_cont_checked(lines, index)?;
    let interpolation_fields = head
        .n1
        .checked_mul(2)
        .ok_or("TAB1 interpolation field count overflows")?;
    require_payload_fields(
        lines,
        next,
        interpolation_fields,
        "TAB1 interpolation payload",
    )?;
    let mut raw_interpolation = Vec::with_capacity(interpolation_fields);
    while raw_interpolation.len() < interpolation_fields {
        let fields = checked_fields(line_at(lines, next, "TAB1 interpolation payload")?)?;
        for field in fields {
            if raw_interpolation.len() == interpolation_fields {
                break;
            }
            raw_interpolation.push(parse_endf_i32(field)?);
        }
        next += 1;
    }
    let mut interpolation = Vec::with_capacity(head.n1);
    let mut previous = 0usize;
    for pair in raw_interpolation.as_chunks::<2>().0 {
        let nbt = usize::try_from(pair[0]).map_err(|_| format!("negative TAB1 NBT {}", pair[0]))?;
        if nbt <= previous || nbt > head.n2 {
            return Err(format!(
                "invalid TAB1 NBT {nbt}; previous={previous}, NP={}",
                head.n2
            ));
        }
        if !(1..=5).contains(&pair[1]) {
            return Err(format!("unsupported TAB1 interpolation INT={}", pair[1]));
        }
        interpolation.push((nbt, pair[1]));
        previous = nbt;
    }
    if head.n2 > 0 && (interpolation.is_empty() || previous != head.n2) {
        return Err(format!(
            "TAB1 interpolation ends at NBT={previous}, expected NP={}",
            head.n2
        ));
    }

    let point_fields = head
        .n2
        .checked_mul(2)
        .ok_or("TAB1 point field count overflows")?;
    require_payload_fields(lines, next, point_fields, "TAB1 point payload")?;
    let mut raw_points = Vec::with_capacity(point_fields);
    while raw_points.len() < point_fields {
        let fields = checked_fields(line_at(lines, next, "TAB1 point payload")?)?;
        for field in fields {
            if raw_points.len() == point_fields {
                break;
            }
            raw_points.push(parse_endf_float(field)?);
        }
        next += 1;
    }
    let points: Vec<(f64, f64)> = raw_points
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| (pair[0], pair[1]))
        .collect();
    if points.windows(2).any(|pair| pair[1].0 < pair[0].0) {
        return Err("TAB1 abscissae are not nondecreasing".into());
    }
    Ok((
        CheckedTab1Record {
            head,
            interpolation,
            points,
        },
        next,
    ))
}

pub fn read_tab2_checked(
    lines: &[&str],
    index: usize,
) -> Result<(CheckedTab2Record, usize), String> {
    let (head, mut next) = read_cont_checked(lines, index)?;
    let interpolation_fields = head
        .n1
        .checked_mul(2)
        .ok_or("TAB2 interpolation field count overflows")?;
    require_payload_fields(
        lines,
        next,
        interpolation_fields,
        "TAB2 interpolation payload",
    )?;
    let mut raw = Vec::with_capacity(interpolation_fields);
    while raw.len() < interpolation_fields {
        let fields = checked_fields(line_at(lines, next, "TAB2 interpolation payload")?)?;
        for field in fields {
            if raw.len() == interpolation_fields {
                break;
            }
            raw.push(parse_endf_i32(field)?);
        }
        next += 1;
    }
    let mut interpolation = Vec::with_capacity(head.n1);
    let mut previous = 0usize;
    for pair in raw.as_chunks::<2>().0 {
        let nbt = usize::try_from(pair[0]).map_err(|_| format!("negative TAB2 NBT {}", pair[0]))?;
        if nbt <= previous || nbt > head.n2 {
            return Err(format!(
                "invalid TAB2 NBT {nbt}; previous={previous}, NZ={}",
                head.n2
            ));
        }
        // TAB2 permits ordinary, corresponding-points (11--15), and unit-base (21--25) interpolation.  The
        // activation reader consumes MF=6 distribution bodies structurally, so retaining these declared schemes is
        // sufficient; TAB1 cross sections remain restricted to the independently integrated one-dimensional laws.
        if !matches!(pair[1], 1..=5 | 11..=15 | 21..=25) {
            return Err(format!("unsupported TAB2 interpolation INT={}", pair[1]));
        }
        interpolation.push((nbt, pair[1]));
        previous = nbt;
    }
    if head.n2 > 0 && (interpolation.is_empty() || previous != head.n2) {
        return Err(format!(
            "TAB2 interpolation ends at NBT={previous}, expected NZ={}",
            head.n2
        ));
    }
    Ok((
        CheckedTab2Record {
            head,
            interpolation,
        },
        next,
    ))
}

#[derive(Clone, Debug)]
pub struct Section<'a> {
    pub mat: i32,
    pub mf: i32,
    pub mt: i32,
    pub lines: Vec<&'a str>,
}

/// Split a complete ENDF tape into checked sections. Control records are consumed but not returned.
pub fn parse_sections(text: &str) -> Result<Vec<Section<'_>>, String> {
    let mut sections: Vec<Section<'_>> = Vec::new();
    let mut active: Option<Section<'_>> = None;
    let mut seen = HashSet::new();
    let line_count = text.lines().count();
    let trailing_blank = text
        .lines()
        .rev()
        .take_while(|line| line.strip_suffix('\r').unwrap_or(line).is_empty())
        .count();
    let record_count = line_count - trailing_blank;
    for (line_number, raw_line) in text.lines().enumerate() {
        let line = raw_line.strip_suffix('\r').unwrap_or(raw_line);
        if line.is_empty() {
            if line_number >= record_count {
                continue;
            }
            return Err(format!("blank ENDF record at line {}", line_number + 1));
        }
        let (mat, mf, mt) =
            checked_tail(line).map_err(|error| format!("line {}: {error}", line_number + 1))?;
        match active.as_mut() {
            Some(section) if mat == section.mat && mf == section.mf && mt == section.mt => {
                section.lines.push(line);
            }
            Some(section) if mat == section.mat && mf == section.mf && mt == 0 => {
                let finished = active.take().expect("active section");
                sections.push(finished);
            }
            Some(section) => {
                return Err(format!(
                    "line {}: MF={}/MT={} section changed to MAT={mat}/MF={mf}/MT={mt} without SEND",
                    line_number + 1,
                    section.mf,
                    section.mt
                ));
            }
            None if mat > 0 && mf > 0 && mt > 0 => {
                if !seen.insert((mat, mf, mt)) {
                    return Err(format!(
                        "line {}: duplicate MAT={mat}/MF={mf}/MT={mt} section",
                        line_number + 1
                    ));
                }
                active = Some(Section {
                    mat,
                    mf,
                    mt,
                    lines: vec![line],
                });
            }
            None => {}
        }
    }
    if let Some(section) = active {
        return Err(format!(
            "MAT={}/MF={}/MT={} section ended without SEND",
            section.mat, section.mf, section.mt
        ));
    }
    Ok(sections)
}

/// Read a LIST record starting at `i`: returns ((C1, C2, L1, L2, N1, N2, values), next index).
pub fn read_list(lines: &[&str], mut i: usize) -> (ListRecord, usize) {
    let f = fields(lines[i]);
    let (c1, c2) = (endf_float(f[0]), endf_float(f[1]));
    let l1 = f[2].trim().parse::<i32>().unwrap_or(0);
    let l2 = f[3].trim().parse::<i32>().unwrap_or(0);
    let n1 = f[4].trim().parse::<usize>().unwrap_or(0);
    let n2 = f[5].trim().parse::<usize>().unwrap_or(0);
    i += 1;
    let mut vals = Vec::with_capacity(n1);
    while vals.len() < n1 {
        let g = fields(lines[i]);
        for field in g {
            if vals.len() < n1 {
                vals.push(endf_float(field));
            }
        }
        i += 1;
    }
    ((c1, c2, l1, l2, n1, n2, vals), i)
}

/// Read a TAB1 record starting at `i`.
///
/// Returns the CONT fields, `(NBT, INT)` interpolation ranges, `(x, y)` points, and the next
/// record index. ENDF uses one-based `NBT` point numbers; they are preserved here rather than
/// translated so a reader can compare the values directly with the evaluation.
pub fn read_tab1(lines: &[&str], mut i: usize) -> (Tab1Record, usize) {
    let f = fields(lines[i]);
    let (c1, c2) = (endf_float(f[0]), endf_float(f[1]));
    let l1 = f[2].trim().parse::<i32>().unwrap_or(0);
    let l2 = f[3].trim().parse::<i32>().unwrap_or(0);
    let nr = f[4].trim().parse::<usize>().unwrap_or(0);
    let np = f[5].trim().parse::<usize>().unwrap_or(0);
    i += 1;

    let mut raw_ranges = Vec::with_capacity(2 * nr);
    while raw_ranges.len() < 2 * nr {
        let g = fields(lines[i]);
        for field in g {
            if raw_ranges.len() == 2 * nr {
                break;
            }
            raw_ranges.push(field.trim().parse::<i32>().unwrap_or(0));
        }
        i += 1;
    }
    let ranges = raw_ranges
        .chunks(2)
        .map(|v| (v[0].max(0) as usize, v[1]))
        .collect();

    let mut raw_xy = Vec::with_capacity(2 * np);
    while raw_xy.len() < 2 * np {
        let g = fields(lines[i]);
        for field in g {
            if raw_xy.len() == 2 * np {
                break;
            }
            raw_xy.push(endf_float(field));
        }
        i += 1;
    }
    let xy = raw_xy.chunks(2).map(|v| (v[0], v[1])).collect();
    ((c1, c2, l1, l2, ranges, xy), i)
}

#[cfg(test)]
mod tests {
    use super::{
        fields, parse_endf_float, read_list_checked, read_tab1_checked, read_tab2_checked, tail,
    };

    fn record(fields: [&str; 6]) -> String {
        let data: String = fields
            .into_iter()
            .map(|value| format!("{value:>11}"))
            .collect();
        format!("{data}{:>4}{:>2}{:>3}{:>5}", 1, 1, 1, 1)
    }

    #[test]
    fn exponent_without_e_is_rounded_once() {
        let parsed = parse_endf_float("4.65000+5").unwrap();
        let explicit = "4.65000e+5".parse::<f64>().unwrap();
        let formerly_double_rounded = 4.65_f64 * 10_f64.powi(5);
        assert_eq!(parsed.to_bits(), explicit.to_bits());
        assert_ne!(parsed.to_bits(), formerly_double_rounded.to_bits());

        for (endf, normalized) in [
            ("-1.23456-3", "-1.23456e-3"),
            (" 1.00000+10", "1.00000e+10"),
            ("+9.87654-2", "+9.87654e-2"),
        ] {
            assert_eq!(
                parse_endf_float(endf).unwrap().to_bits(),
                normalized.parse::<f64>().unwrap().to_bits()
            );
        }
    }

    #[test]
    fn ordinary_blank_and_invalid_fields_retain_their_semantics() {
        assert_eq!(parse_endf_float(" ").unwrap(), 0.0);
        assert_eq!(parse_endf_float("1.25e+3").unwrap(), 1250.0);
        assert!(parse_endf_float("not-a-float").is_err());
        assert!(parse_endf_float("1.0e999").is_err());
    }

    #[test]
    fn checked_records_reject_counts_larger_than_the_remaining_payload() {
        let list = record(["0", "0", "0", "0", "2000000000", "0"]);
        assert!(read_list_checked(&[&list], 0)
            .unwrap_err()
            .contains("at most 0 remain"));

        let tab1 = record(["0", "0", "0", "0", "2000000000", "1"]);
        assert!(read_tab1_checked(&[&tab1], 0)
            .unwrap_err()
            .contains("at most 0 remain"));

        let tab2 = record(["0", "0", "0", "0", "2000000000", "1"]);
        assert!(read_tab2_checked(&[&tab2], 0)
            .unwrap_err()
            .contains("at most 0 remain"));
    }

    #[test]
    fn fixed_width_helpers_reject_non_ascii_without_slicing_inside_a_codepoint() {
        let unicode = "é".repeat(40);
        assert_eq!(tail(&unicode), None);
        assert_eq!(fields(&unicode), [""; 6]);
    }
}
