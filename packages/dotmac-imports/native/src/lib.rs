//! Bounded CSV row transformations. Python's native CSV decoder owns grammar.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

// Python str.strip includes the four information separators in addition to
// Unicode White_Space; Rust char::is_whitespace alone is not equivalent.
const WHITESPACE: &[&[u8]] = &[
    b"\t", b"\n", b"\x0b", b"\x0c", b"\r", b"\x1c", b"\x1d", b"\x1e", b"\x1f", b" ",
    b"\xc2\x85", b"\xc2\xa0", b"\xe1\x9a\x80", b"\xe2\x80\x80", b"\xe2\x80\x81",
    b"\xe2\x80\x82", b"\xe2\x80\x83", b"\xe2\x80\x84", b"\xe2\x80\x85",
    b"\xe2\x80\x86", b"\xe2\x80\x87", b"\xe2\x80\x88", b"\xe2\x80\x89",
    b"\xe2\x80\x8a", b"\xe2\x80\xa8", b"\xe2\x80\xa9", b"\xe2\x80\xaf",
    b"\xe2\x81\x9f", b"\xe3\x80\x80",
];

// Reversible UTF-8/surrogatepass wire: Python accepts lone surrogates in text
// decoded by some codecs. Never silently constrain that contract to Rust UTF-8.
fn trimmed(mut value: &[u8]) -> &[u8] {
    while let Some(rest) = WHITESPACE.iter().find_map(|space| value.strip_prefix(*space)) {
        value = rest;
    }
    while let Some(rest) = WHITESPACE.iter().find_map(|space| value.strip_suffix(*space)) {
        value = rest;
    }
    value
}

type Record = (usize, Vec<Vec<u8>>);
type Row = Vec<Vec<u8>>;

#[pyfunction]
fn normalize_rows(
    py: Python<'_>,
    column_count: usize,
    records: Vec<Record>,
    remaining: usize,
    projection: Vec<usize>,
) -> PyResult<Vec<Vec<Py<PyBytes>>>> {
    let result: Result<Vec<Row>, String> = py.detach(move || {
        let mut output = Vec::with_capacity(records.len().min(remaining));
        for (line, values) in records {
            if values.len() > column_count {
                return Err(format!(
                    "row {line} has {} cells for {} headings — extra cells cannot be silently discarded",
                    values.len(), column_count
                ));
            }
            if values.iter().all(|value| trimmed(value).is_empty()) {
                continue;
            }
            if output.len() >= remaining {
                return Err("row limit exceeded".to_owned());
            }
            let row = projection.iter().map(|index| {
                let value = values.get(*index).map_or(&[][..], Vec::as_slice);
                trimmed(value).to_vec()
            }).collect();
            output.push(row);
        }
        Ok(output)
    });
    let rows = result.map_err(PyValueError::new_err)?;
    Ok(rows.into_iter().map(|row| {
        row.into_iter().map(|value| PyBytes::new(py, &value).unbind()).collect()
    }).collect())
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(normalize_rows, module)?)?;
    Ok(())
}
