//! Sequential Decimal batch reductions with a private, static libmpdec.
//!
//! Python captures Decimal strings and context; this module owns the detached
//! loops. The shim keeps libmpdec layouts, allocation and signal bits in C.

use std::ffi::CString;

use dotmac_decimal_core::{valid_context, Context, ContextSpec, Decimal, MEMORY};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type BatchResult = (Vec<String>, u32, Option<(u32, usize, usize)>);

fn reduce(columns: Vec<Vec<CString>>, spec: ContextSpec, traps: u32) -> BatchResult {
    let mut results = Vec::with_capacity(columns.len());
    let mut status = 0;
    let Some(context) = Context::new(spec) else {
        return (results, MEMORY, None);
    };
    let (Some(mut total), Some(mut value), Some(mut scale)) =
        (Decimal::new(), Decimal::new(), Decimal::new())
    else {
        return (results, MEMORY, None);
    };
    let scale_text = c"0.000001";
    status |= scale.parse(scale_text).signals;
    if status & MEMORY != 0 {
        return (results, status, None);
    }
    for (column_index, column) in columns.iter().enumerate() {
        status |= total.parse(c"0").signals;
        if status & MEMORY != 0 {
            return (results, status, None);
        }
        for (row_index, text) in column.iter().enumerate() {
            // Canonical Python Decimal strings parse exactly, without applying
            // arithmetic precision/rounding or invoking Python per item.
            let parsed = value.parse(text).signals;
            status |= parsed;
            if parsed & (traps | MEMORY) != 0 {
                return (results, status, Some((parsed, column_index, row_index)));
            }
            let raised = total.add(&value, &context).signals;
            status |= raised;
            if raised & (traps | MEMORY) != 0 {
                return (results, status, Some((raised, column_index, row_index)));
            }
        }
        let raised = total.quantize(&scale, &context, Some(4)).signals;
        status |= raised;
        if raised & (traps | MEMORY) != 0 {
            return (results, status, Some((raised, column_index, column.len())));
        }
        let Some(text) = total.text() else {
            return (results, status | MEMORY, None);
        };
        results.push(text);
    }
    (results, status, None)
}

#[pyfunction]
fn sum_quantized_columns(
    py: Python<'_>,
    columns: Vec<Vec<String>>,
    context: ContextSpec,
    traps: u32,
) -> PyResult<BatchResult> {
    if !valid_context(context) || traps & !0x1ff != 0 {
        return Err(PyValueError::new_err("invalid Decimal batch context"));
    }
    let columns: Vec<Vec<CString>> = columns
        .into_iter()
        .map(|column| column.into_iter().map(CString::new).collect())
        .collect::<Result<_, _>>()
        .map_err(|_| PyValueError::new_err("Decimal text contains NUL"))?;
    Ok(py.detach(move || reduce(columns, context, traps)))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sum_quantized_columns, module)?)
}
