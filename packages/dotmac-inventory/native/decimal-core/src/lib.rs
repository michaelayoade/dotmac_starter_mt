//! Private, Python-independent exact Decimal primitives over bundled libmpdec.
//!
//! Callers own algorithms and signal replay. Every arithmetic operation is
//! quiet, reports only its own status, and uses an explicitly owned context.
//! Handles remain within one worker thread; this crate never changes global
//! allocation, trap handlers, or thread-local arithmetic context.

use std::cmp::Ordering;
use std::ffi::{c_char, c_void, CStr};
use std::ptr::NonNull;

pub const INVALID: u32 = 1 << 0;
pub const DIVISION_BY_ZERO: u32 = 1 << 2;
pub const INEXACT: u32 = 1 << 6;
pub const ROUNDED: u32 = 1 << 7;
pub const MEMORY: u32 = 1 << 9;
pub const MAX_CONTEXT: i64 = 999_999_999_999_999_999;
/// Precision, rounding code, Emin, Emax, clamp. Rounding codes follow CPython:
/// ceiling, floor, up, down, half-up, half-down, half-even, 05up.
pub type ContextSpec = (i64, u8, i64, i64, u8);

/// Signal bits follow CPython priority (InvalidOperation through Clamped), with
/// allocation failure at bit 9. Invalid condition bits preserve exception args:
/// InvalidOperation=0, ConversionSyntax=1, DivisionImpossible=2,
/// DivisionUndefined=3, InvalidContext=4. Memory failures have no condition arg.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Status {
    pub signals: u32,
    pub conditions: u32,
}
impl Status {
    fn from_wire(wire: u64) -> Self {
        Self {
            signals: wire as u32,
            conditions: (wire >> 32) as u32,
        }
    }
}

pub fn valid_context(spec: ContextSpec) -> bool {
    (1..=MAX_CONTEXT).contains(&spec.0)
        && spec.1 <= 7
        && (-MAX_CONTEXT..=0).contains(&spec.2)
        && (0..=MAX_CONTEXT).contains(&spec.3)
        && spec.4 <= 1
}

extern "C" {
    fn dm_context_new(
        precision: i64,
        rounding: i32,
        emin: i64,
        emax: i64,
        clamp: i32,
    ) -> *mut c_void;
    fn dm_context_clone(context: *const c_void) -> *mut c_void;
    fn dm_context_free(context: *mut c_void);
    fn dm_decimal_new() -> *mut c_void;
    fn dm_decimal_free(decimal: *mut c_void);
    fn dm_decimal_parse(decimal: *mut c_void, text: *const c_char) -> u64;
    fn dm_decimal_copy(result: *mut c_void, value: *const c_void) -> u64;
    fn dm_decimal_add(
        result: *mut c_void,
        left: *const c_void,
        right: *const c_void,
        context: *const c_void,
    ) -> u64;
    fn dm_decimal_sub(
        result: *mut c_void,
        left: *const c_void,
        right: *const c_void,
        context: *const c_void,
    ) -> u64;
    fn dm_decimal_mul(
        result: *mut c_void,
        left: *const c_void,
        right: *const c_void,
        context: *const c_void,
    ) -> u64;
    fn dm_decimal_div(
        result: *mut c_void,
        left: *const c_void,
        right: *const c_void,
        context: *const c_void,
    ) -> u64;
    fn dm_decimal_scaleb(
        result: *mut c_void,
        left: *const c_void,
        right: *const c_void,
        context: *const c_void,
    ) -> u64;
    fn dm_decimal_compare(left: *const c_void, right: *const c_void, order: *mut i32) -> u64;
    fn dm_decimal_quantize(
        result: *mut c_void,
        value: *const c_void,
        scale: *const c_void,
        context: *const c_void,
        rounding: i32,
    ) -> u64;
    fn dm_decimal_string(decimal: *const c_void) -> *mut c_char;
    fn dm_string_free(text: *mut c_char);
}

pub struct Context(NonNull<c_void>);
impl Context {
    /// Returns None for invalid fields or allocation failure. Validate first to
    /// distinguish invalid input from allocation failure at a language boundary.
    pub fn new(spec: ContextSpec) -> Option<Self> {
        if !valid_context(spec) {
            return None;
        }
        // SAFETY: validated fields; C owns allocation and initializes every field.
        NonNull::new(unsafe {
            dm_context_new(spec.0, spec.1.into(), spec.2, spec.3, spec.4.into())
        })
        .map(Self)
    }

    pub fn try_clone(&self) -> Option<Self> {
        // SAFETY: live context, independent returned allocation or NULL.
        NonNull::new(unsafe { dm_context_clone(self.0.as_ptr()) }).map(Self)
    }
}
impl Drop for Context {
    fn drop(&mut self) {
        // SAFETY: exclusive ownership of this context allocation.
        unsafe { dm_context_free(self.0.as_ptr()) }
    }
}

pub struct Decimal(NonNull<c_void>);

macro_rules! binary_method {
    ($name:ident, $ffi:ident) => {
        /// Replace this value with a quiet operation using the explicit context.
        pub fn $name(&mut self, value: &Self, context: &Context) -> Status {
            // SAFETY: mpdec permits result/left aliasing; all handles are live
            // and exclusively owned. The function cannot retain these pointers.
            Status::from_wire(unsafe {
                $ffi(
                    self.0.as_ptr(),
                    self.0.as_ptr(),
                    value.0.as_ptr(),
                    context.0.as_ptr(),
                )
            })
        }
    };
}

impl Decimal {
    pub fn new() -> Option<Self> {
        // SAFETY: quiet allocator returns exclusively owned storage or NULL.
        let mut result = Self(NonNull::new(unsafe { dm_decimal_new() })?);
        // mpd_qnew allocates a coefficient buffer but does not initialize a
        // numerical value. Establish a valid zero before exposing a safe handle.
        if result.parse(c"0").signals & MEMORY != 0 {
            return None;
        }
        Some(result)
    }

    /// Exact context-independent parsing. Caller supplies canonical Decimal text.
    pub fn parse(&mut self, text: &CStr) -> Status {
        // SAFETY: live handle, NUL-terminated input and quiet conversion.
        Status::from_wire(unsafe { dm_decimal_parse(self.0.as_ptr(), text.as_ptr()) })
    }

    pub fn copy_from(&mut self, value: &Self) -> Status {
        // SAFETY: live handles; quiet exact copy does not apply context rounding.
        Status::from_wire(unsafe { dm_decimal_copy(self.0.as_ptr(), value.0.as_ptr()) })
    }

    binary_method!(add, dm_decimal_add);
    binary_method!(sub, dm_decimal_sub);
    binary_method!(mul, dm_decimal_mul);
    binary_method!(div, dm_decimal_div);
    binary_method!(scaleb, dm_decimal_scaleb);

    /// Ordered comparison: any NaN reports InvalidOperation and no ordering.
    /// This intentionally follows Python ordered comparison, not quiet equality.
    pub fn compare(&self, value: &Self) -> (Option<Ordering>, Status) {
        let mut order = 0;
        // SAFETY: read-only live handles and stack-owned output integer.
        let status = Status::from_wire(unsafe {
            dm_decimal_compare(self.0.as_ptr(), value.0.as_ptr(), &mut order)
        });
        let ordering = match order {
            -1 => Some(Ordering::Less),
            0 => Some(Ordering::Equal),
            1 => Some(Ordering::Greater),
            _ => None,
        };
        (ordering, status)
    }

    /// Quantize with ambient rounding (None) or one explicit rounding override.
    /// Invalid override codes report InvalidContext without accessing the table.
    pub fn quantize(&mut self, scale: &Self, context: &Context, rounding: Option<u8>) -> Status {
        // SAFETY: mpdec permits result/value aliasing. The C shim copies context
        // and validates override before applying it; caller context is unchanged.
        Status::from_wire(unsafe {
            dm_decimal_quantize(
                self.0.as_ptr(),
                self.0.as_ptr(),
                scale.0.as_ptr(),
                context.0.as_ptr(),
                rounding.map(i32::from).unwrap_or(-1),
            )
        })
    }

    pub fn text(&self) -> Option<String> {
        // SAFETY: context-independent serialization returns ASCII or NULL.
        let pointer = NonNull::new(unsafe { dm_decimal_string(self.0.as_ptr()) })?;
        let result = unsafe { CStr::from_ptr(pointer.as_ptr()) }
            .to_string_lossy()
            .into_owned();
        // SAFETY: matching deallocator frees the returned string exactly once.
        unsafe { dm_string_free(pointer.as_ptr()) };
        Some(result)
    }
}
impl Drop for Decimal {
    fn drop(&mut self) {
        // SAFETY: exclusive ownership of this decimal and its coefficient.
        unsafe { dm_decimal_free(self.0.as_ptr()) }
    }
}
