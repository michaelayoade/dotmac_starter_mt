//! Bounded CSV row transformations. Python's native CSV decoder owns grammar.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

// Python str.strip includes the four information separators in addition to
// Unicode White_Space; Rust char::is_whitespace alone is not equivalent.
fn whitespace(value: char) -> bool {
    value.is_whitespace() || ('\u{001c}'..='\u{001f}').contains(&value)
}

type Record = (usize, Vec<String>);
type Row = Vec<(String, String)>;

#[pyfunction]
fn normalize_rows(
    py: Python<'_>,
    columns: Vec<String>,
    records: Vec<Record>,
    remaining: usize,
) -> PyResult<Vec<Row>> {
    let result: Result<Vec<Row>, String> = py.detach(move || {
        let mut output = Vec::with_capacity(records.len().min(remaining));
        for (line, values) in records {
            if values.len() > columns.len() {
                return Err(format!(
                    "row {line} has {} cells for {} headings — extra cells cannot be silently discarded",
                    values.len(), columns.len()
                ));
            }
            if values.iter().all(|value| value.trim_matches(whitespace).is_empty()) {
                continue;
            }
            if output.len() >= remaining {
                return Err("row limit exceeded".to_owned());
            }
            let row = columns.iter().enumerate().filter(|(_, column)| !column.is_empty())
                .map(|(index, column)| {
                    let value = values.get(index).map_or("", String::as_str);
                    (column.clone(), value.trim_matches(whitespace).to_owned())
                }).collect();
            output.push(row);
        }
        Ok(output)
    });
    result.map_err(PyValueError::new_err)
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(normalize_rows, module)?)?;
    Ok(())
}
