use std::cmp::Ordering;

use dotmac_decimal_core::{valid_context, Context, Decimal, INEXACT, INVALID, ROUNDED};

fn decimal(text: &std::ffi::CStr) -> Decimal {
    let mut value = Decimal::new().unwrap();
    assert_eq!(value.parse(text).signals, 0);
    value
}

#[test]
fn arithmetic_and_copy_use_explicit_context() {
    let context = Context::new((28, 6, -999999, 999999, 0)).unwrap();
    let cloned = context.try_clone().unwrap();
    let mut value = decimal(c"10");
    let operand = decimal(c"3");
    assert_eq!(value.add(&operand, &context).signals, 0);
    assert_eq!(value.sub(&operand, &cloned).signals, 0);
    assert_eq!(value.mul(&operand, &context).signals, 0);
    assert_eq!(value.div(&operand, &context).signals, 0);
    assert_eq!(value.text().as_deref(), Some("10"));
    let mut copy = Decimal::new().unwrap();
    assert_eq!(copy.copy_from(&value).signals, 0);
    assert_eq!(copy.compare(&operand), (Some(Ordering::Greater), Default::default()));
    assert_eq!(copy.scaleb(&decimal(c"-1"), &context).signals, 0);
    assert_eq!(copy.text().as_deref(), Some("1.0"));
}

#[test]
fn quantize_override_does_not_change_ambient_rounding() {
    let context = Context::new((28, 6, -999999, 999999, 0)).unwrap();
    let scale = decimal(c"0.01");
    let mut value = decimal(c"1.225");
    assert_eq!(value.quantize(&scale, &context, Some(4)).signals, INEXACT | ROUNDED);
    assert_eq!(value.text().as_deref(), Some("1.23"));
    value.parse(c"1.225");
    assert_eq!(value.quantize(&scale, &context, None).signals, INEXACT | ROUNDED);
    assert_eq!(value.text().as_deref(), Some("1.22"));
}

#[test]
fn division_and_comparison_retain_invalid_condition_identity() {
    let context = Context::new((28, 6, -999999, 999999, 0)).unwrap();
    let mut value = decimal(c"0");
    let status = value.div(&decimal(c"0"), &context);
    assert_eq!(status.signals, INVALID);
    assert_eq!(status.conditions, 1 << 3); // decimal.DivisionUndefined
    let (order, status) = value.compare(&decimal(c"1"));
    assert_eq!(order, None);
    assert_eq!(status.signals, INVALID);
    assert_eq!(status.conditions, 1 << 0); // decimal.InvalidOperation
}

#[test]
fn context_validation_rejects_invalid_rounding_and_limits() {
    assert_eq!(Decimal::new().unwrap().text().as_deref(), Some("0"));
    assert!(!valid_context((0, 6, -999999, 999999, 0)));
    assert!(!valid_context((28, 8, -999999, 999999, 0)));
    assert!(!valid_context((28, 6, 1, 999999, 0)));
    assert!(Context::new((28, 8, -999999, 999999, 0)).is_none());
}
