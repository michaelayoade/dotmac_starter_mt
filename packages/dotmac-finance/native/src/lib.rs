//! Ordered fixed-asset charge trajectories; persistence remains Python-owned.
use dotmac_decimal_core::{valid_context, Context, ContextSpec, Decimal, Status};
use num_bigint::BigUint;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::cmp::Ordering;
use std::ffi::CString;

const MEMORY: u32 = 1 << 9;
type Batch = (Vec<String>, u32, Option<(u32, u32)>, bool);
#[derive(Clone, Copy)]
enum Stop {
    Signal(Status),
    Policy,
}
struct Machine {
    context: Context,
    traps: u32,
    signals: u32,
    scale: i64,
}
impl Machine {
    fn check(&mut self, status: Status) -> Result<(), Stop> {
        self.signals |= status.signals;
        if status.signals & (self.traps | MEMORY) != 0 {
            Err(Stop::Signal(status))
        } else {
            Ok(())
        }
    }
    fn memory(&mut self) -> Stop {
        self.signals |= MEMORY;
        Stop::Signal(Status {
            signals: MEMORY,
            conditions: 0,
        })
    }
    fn parse(&mut self, text: &str) -> Result<Decimal, Stop> {
        let mut value = Decimal::new().ok_or_else(|| self.memory())?;
        let text = CString::new(text).map_err(|_| self.memory())?;
        self.check(value.parse(&text))?;
        Ok(value)
    }
    fn copy(&mut self, value: &Decimal) -> Result<Decimal, Stop> {
        let mut result = Decimal::new().ok_or_else(|| self.memory())?;
        self.check(result.copy_from(value))?;
        Ok(result)
    }
    fn sub(&mut self, left: &Decimal, right: &Decimal) -> Result<Decimal, Stop> {
        let mut result = self.copy(left)?;
        self.check(result.sub(right, &self.context))?;
        Ok(result)
    }
    fn choose(
        &mut self,
        first: &Decimal,
        second: &Decimal,
        minimum: bool,
    ) -> Result<Decimal, Stop> {
        // Python min/max compares each later operand to the first winner.
        // Equal or unordered operands retain the first operand, including its sign.
        let (order, status) = second.compare(first);
        self.check(status)?;
        let replace = if minimum {
            order == Some(Ordering::Less)
        } else {
            order == Some(Ordering::Greater)
        };
        self.copy(if replace { second } else { first })
    }
    fn quantize(&mut self, mut value: Decimal) -> Result<Decimal, Stop> {
        if !(0..=6).contains(&self.scale) {
            return Err(Stop::Policy);
        }
        // Decimal(1).scaleb(-minor_units) is an ambient-context operation at
        // EVERY original quantization point, before the HALF_UP quantize.
        let mut quantum = self.parse("1")?;
        let exponent = self.parse(&(-self.scale).to_string())?;
        self.check(quantum.scaleb(&exponent, &self.context))?;
        self.check(value.quantize(&quantum, &self.context, Some(4)))?;
        Ok(value)
    }
    fn charge(
        &mut self,
        carrying: &Decimal,
        residual: &Decimal,
        useful: &Decimal,
        remaining: &Decimal,
        periods: &BigUint,
        method: &str,
        remaining_count: &BigUint,
    ) -> Result<Decimal, Stop> {
        let zero = self.parse("0")?;
        if method == "straight_line" {
            let difference = self.sub(carrying, residual)?;
            let mut charge = self.choose(&zero, &difference, false)?;
            let periods_value = self.parse(&periods.to_string())?;
            self.check(charge.mul(&periods_value, &self.context))?;
            self.check(charge.div(remaining, &self.context))?;
            return self.quantize(charge);
        }
        let multiplier = self.parse(if method == "double_declining" {
            "2"
        } else {
            "1"
        })?;
        let mut current = self.copy(carrying)?;
        let mut period = BigUint::from(0u8);
        while &period < periods {
            let difference = self.sub(&current, residual)?;
            let maximum = self.choose(&zero, &difference, false)?;
            // maximum is max(0, difference): it is either nonnegative finite,
            // +Infinity, or the first operand zero after an unordered compare.
            let (order, status) = maximum.compare(&zero);
            self.check(status)?;
            if order == Some(Ordering::Equal) {
                break;
            }
            let charge = if &(&period + 1u8) == remaining_count {
                self.copy(&maximum)?
            } else {
                let mut tentative = self.copy(&current)?;
                self.check(tentative.mul(&multiplier, &self.context))?;
                self.check(tentative.div(useful, &self.context))?;
                let tentative = self.quantize(tentative)?;
                self.choose(&maximum, &tentative, true)?
            };
            self.check(current.sub(&charge, &self.context))?;
            period += 1u8;
        }
        let difference = self.sub(carrying, &current)?;
        self.quantize(difference)
    }
}

fn compute(
    values: Vec<String>,
    periods: BigUint,
    remaining_count: BigUint,
    method: String,
    scale: i64,
    spec: ContextSpec,
    traps: u32,
) -> Batch {
    let Some(context) = Context::new(spec) else {
        return (Vec::new(), MEMORY, None, false);
    };
    let mut machine = Machine {
        context,
        traps,
        signals: 0,
        scale,
    };
    let result = (|| -> Result<Vec<String>, Stop> {
        let carrying = machine.parse(&values[0])?;
        let residual = machine.parse(&values[1])?;
        let unimpaired = machine.parse(&values[2])?;
        let useful = machine.parse(&values[3])?;
        let remaining = machine.parse(&values[4])?;
        let charge = machine.charge(
            &carrying,
            &residual,
            &useful,
            &remaining,
            &periods,
            &method,
            &remaining_count,
        )?;
        let unimpaired_charge = machine.charge(
            &unimpaired,
            &residual,
            &useful,
            &remaining,
            &periods,
            &method,
            &remaining_count,
        )?;
        Ok(vec![
            charge.text().ok_or_else(|| machine.memory())?,
            unimpaired_charge.text().ok_or_else(|| machine.memory())?,
        ])
    })();
    match result {
        Ok(results) => (results, machine.signals, None, false),
        Err(Stop::Signal(status)) => (
            Vec::new(),
            machine.signals,
            Some((status.signals, status.conditions)),
            false,
        ),
        Err(Stop::Policy) => (Vec::new(), machine.signals, None, true),
    }
}

#[pyfunction]
fn depreciation_charges(
    py: Python<'_>,
    values: Vec<String>,
    periods: String,
    method: String,
    minor_units: i64,
    context: ContextSpec,
    traps: u32,
) -> PyResult<Batch> {
    if !valid_context(context)
        || traps & !0x1ff != 0
        || values.len() != 5
        || !matches!(
            method.as_str(),
            "straight_line" | "declining_balance" | "double_declining"
        )
        || values.iter().any(|value| value.contains('\0'))
    {
        return Err(PyValueError::new_err(
            "invalid depreciation computation snapshot",
        ));
    }
    let periods = BigUint::parse_bytes(periods.as_bytes(), 10)
        .ok_or_else(|| PyValueError::new_err("invalid depreciation period count"))?;
    let remaining_count = BigUint::parse_bytes(values[4].as_bytes(), 10)
        .ok_or_else(|| PyValueError::new_err("invalid depreciation remaining life"))?;
    Ok(py.detach(move || {
        compute(
            values,
            periods,
            remaining_count,
            method,
            minor_units,
            context,
            traps,
        )
    }))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(depreciation_charges, module)?)
}
