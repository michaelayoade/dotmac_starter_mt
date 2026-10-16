//! Primitive geometry for the positioning owner. No tenancy or persistence policy.

use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;

type Point = (f64, f64);
type Circle = (f64, f64, f64);
type Shape = (Option<Circle>, Vec<Point>);

fn haversine_m(a: Point, b: Point) -> f64 {
    // Keep the Python owner's radius, operation order and antipodal clamp.
    let phi_a = a.0.to_radians();
    let phi_b = b.0.to_radians();
    let delta_phi = (b.0 - a.0).to_radians();
    let delta_lambda = (b.1 - a.1).to_radians();
    let value = (delta_phi / 2.0).sin().powi(2)
        + phi_a.cos() * phi_b.cos() * (delta_lambda / 2.0).sin().powi(2);
    2.0 * 6_371_000.0 * value.sqrt().min(1.0).asin()
}

fn inside_polygon(latitude: f64, longitude: f64, points: &[Point]) -> bool {
    // Empty polygons are rejected before detaching, so this indexing is bounded.
    let mut previous = points[points.len() - 1];
    let mut inside = false;
    for &current in points {
        let (current_y, current_x) = current;
        let (previous_y, previous_x) = previous;
        if (current_y > latitude) != (previous_y > latitude) {
            let crossing_x = (previous_x - current_x) * (latitude - current_y)
                / (previous_y - current_y)
                + current_x;
            if longitude < crossing_x {
                inside = !inside;
            }
        }
        previous = current;
    }
    inside
}

/// Return one membership result per supplied shape, preserving input order.
#[pyfunction]
#[pyo3(signature = (*, latitude, longitude, shapes))]
fn contains_batch(
    py: Python<'_>,
    latitude: f64,
    longitude: f64,
    shapes: Vec<Shape>,
) -> PyResult<Vec<bool>> {
    if shapes
        .iter()
        .any(|(circle, points)| circle.is_none() && points.is_empty())
    {
        return Err(PyIndexError::new_err("list index out of range"));
    }
    // Extraction above owns all primitives. No Python objects cross this boundary.
    Ok(py.detach(move || {
        shapes
            .into_iter()
            .map(|(circle, points)| match circle {
                Some((center_latitude, center_longitude, radius_m)) => {
                    haversine_m((center_latitude, center_longitude), (latitude, longitude))
                        <= radius_m
                }
                None => inside_polygon(latitude, longitude, &points),
            })
            .collect()
    }))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(contains_batch, module)?)?;
    Ok(())
}
