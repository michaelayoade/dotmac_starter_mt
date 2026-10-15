//! Sequential Decimal batch reductions with a private, static libmpdec.
//!
//! Python captures Decimal strings and context; this module owns the detached
//! loops. The shim keeps libmpdec layouts, allocation and signal bits in C.

use std::ffi::{c_char, c_void, CStr, CString};
use std::ptr::NonNull;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

const MEMORY: u32 = 1 << 9;
const MAX_CONTEXT: i64 = 999_999_999_999_999_999;
type ContextSpec = (i64, u8, i64, i64, u8);
type BatchResult = (Vec<String>, u32, Option<(u32, usize, usize)>);

extern "C" {
    fn dm_context_new(
        precision: i64,
        rounding: i32,
        emin: i64,
        emax: i64,
        clamp: i32,
    ) -> *mut c_void;
    fn dm_context_free(context: *mut c_void);
    fn dm_decimal_new() -> *mut c_void;
    fn dm_decimal_free(decimal: *mut c_void);
    fn dm_decimal_parse(decimal: *mut c_void, text: *const c_char) -> u32;
    fn dm_decimal_add(
        result: *mut c_void,
        left: *const c_void,
        right: *const c_void,
        context: *const c_void,
    ) -> u32;
    fn dm_decimal_quantize(
        result: *mut c_void,
        value: *const c_void,
        scale: *const c_void,
        context: *const c_void,
    ) -> u32;
    fn dm_decimal_string(decimal: *const c_void) -> *mut c_char;
    fn dm_string_free(text: *mut c_char);
}

struct Context(NonNull<c_void>);
impl Context {
    fn new(spec: ContextSpec) -> Option<Self> {
        // SAFETY: the entry point validates all fields and the C shim owns the
        // allocation. Each batch has its own context; no global state changes.
        NonNull::new(unsafe {
            dm_context_new(spec.0, spec.1.into(), spec.2, spec.3, spec.4.into())
        })
        .map(Self)
    }
}
impl Drop for Context {
    fn drop(&mut self) {
        // SAFETY: this handle exclusively owns this context allocation.
        unsafe { dm_context_free(self.0.as_ptr()) }
    }
}

struct Decimal(NonNull<c_void>);
impl Decimal {
    fn new() -> Option<Self> {
        // SAFETY: the quiet allocator returns either owned storage or NULL.
        NonNull::new(unsafe { dm_decimal_new() }).map(Self)
    }

    fn parse(&mut self, value: &CStr) -> u32 {
        // SAFETY: valid, live handle and NUL-terminated input; quiet parse.
        unsafe { dm_decimal_parse(self.0.as_ptr(), value.as_ptr()) }
    }

    fn add(&mut self, value: &Self, context: &Context) -> u32 {
        // SAFETY: mpd_qadd permits result/left aliasing, as documented by mpdec.
        // All handles are live, locally owned, and only read synchronously.
        unsafe {
            dm_decimal_add(
                self.0.as_ptr(),
                self.0.as_ptr(),
                value.0.as_ptr(),
                context.0.as_ptr(),
            )
        }
    }

    fn quantize(&mut self, scale: &Self, context: &Context) -> u32 {
        // SAFETY: mpd_qquantize permits result/value aliasing. C copies context
        // to override rounding without changing the addition context.
        unsafe {
            dm_decimal_quantize(
                self.0.as_ptr(),
                self.0.as_ptr(),
                scale.0.as_ptr(),
                context.0.as_ptr(),
            )
        }
    }

    fn text(&self) -> Option<String> {
        // SAFETY: serialization returns an allocated ASCII C string or NULL.
        let pointer = NonNull::new(unsafe { dm_decimal_string(self.0.as_ptr()) })?;
        // mpdec emits ASCII only. Lossy conversion is identical for ASCII and
        // avoids a panic crossing the Python boundary on an unexpected byte.
        let result = unsafe { CStr::from_ptr(pointer.as_ptr()) }
            .to_string_lossy()
            .into_owned();
        // SAFETY: the matching mpdec deallocator frees this string exactly once.
        unsafe { dm_string_free(pointer.as_ptr()) };
        Some(result)
    }
}
impl Drop for Decimal {
    fn drop(&mut self) {
        // SAFETY: this handle exclusively owns this decimal and its coefficient.
        unsafe { dm_decimal_free(self.0.as_ptr()) }
    }
}

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
    status |= scale.parse(scale_text);
    if status & MEMORY != 0 {
        return (results, status, None);
    }
    for (column_index, column) in columns.iter().enumerate() {
        status |= total.parse(c"0");
        if status & MEMORY != 0 {
            return (results, status, None);
        }
        for (row_index, text) in column.iter().enumerate() {
            // Canonical Python Decimal strings parse exactly, without applying
            // arithmetic precision/rounding or invoking Python per item.
            let parsed = value.parse(text);
            status |= parsed;
            if parsed & (traps | MEMORY) != 0 {
                return (results, status, Some((parsed, column_index, row_index)));
            }
            let raised = total.add(&value, &context);
            status |= raised;
            if raised & (traps | MEMORY) != 0 {
                return (results, status, Some((raised, column_index, row_index)));
            }
        }
        let raised = total.quantize(&scale, &context);
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
    if !(1..=MAX_CONTEXT).contains(&context.0)
        || context.1 > 7
        || !(-MAX_CONTEXT..=0).contains(&context.2)
        || !(0..=MAX_CONTEXT).contains(&context.3)
        || context.4 > 1
        || traps & !0x1ff != 0
    {
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
