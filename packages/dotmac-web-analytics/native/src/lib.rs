//! Owned primitive analytics computation; Python retains contracts and storage.

use std::collections::{BTreeMap, BTreeSet};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type Event = (String, i64, i64, String, String, i64, i64, i64, Vec<(String, String)>);
type Funnel = (i64, Vec<(String, i64)>);
type Visitor = (String, i64, usize, usize);
type Metric = (i64, String, String, usize, usize, usize);
type FunnelCount = (usize, Vec<usize>);
type Projection = (Vec<Visitor>, Vec<Vec<usize>>, Vec<Metric>, Vec<FunnelCount>);

#[derive(Default)]
struct Cell {
    events: usize,
    visitors: BTreeSet<String>,
    sessions: BTreeSet<usize>,
}

fn project(events: Vec<Event>, timeout_us: i64, funnels: Vec<Funnel>) -> Projection {
    let mut by_visitor: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (index, event) in events.iter().enumerate() {
        by_visitor.entry(event.0.clone()).or_default().push(index);
    }
    // One canonical ordering is reused for visitor summaries, sessions and funnels.
    for ordered in by_visitor.values_mut() {
        ordered.sort_by(|&a, &b| {
            let a = &events[a];
            let b = &events[b];
            (a.1, a.2, &a.3).cmp(&(b.1, b.2, &b.3))
        });
    }
    let mut visitors = Vec::new();
    let mut sessions: Vec<Vec<usize>> = Vec::new();
    let mut session_by_event = vec![0; events.len()];
    for (visitor, ordered) in &by_visitor {
        let first = ordered[0];
        let last = ordered[ordered.len() - 1];
        let version = ordered.iter().map(|&index| events[index].6).max().unwrap();
        visitors.push((visitor.clone(), version, first, last));
        let mut previous = None;
        for &index in ordered {
            if previous.is_none_or(|prior: usize| events[index].1 - events[prior].1 > timeout_us) {
                sessions.push(Vec::new());
            }
            let session_id = sessions.len() - 1;
            sessions[session_id].push(index);
            session_by_event[index] = session_id;
            previous = Some(index);
        }
    }
    let mut cells: BTreeMap<(i64, String, String), Cell> = BTreeMap::new();
    for (index, event) in events.iter().enumerate() {
        for (dimension, key) in &event.8 {
            let cell = cells.entry((event.7, dimension.clone(), key.clone())).or_default();
            cell.events += 1;
            cell.visitors.insert(event.0.clone());
            cell.sessions.insert(session_by_event[index]);
        }
    }
    let metrics = cells.into_iter().map(|((bucket, dimension, key), cell)| {
        (bucket, dimension, key, cell.events, cell.visitors.len(), cell.sessions.len())
    }).collect();
    let counts = funnels.into_iter().map(|(within_seconds, steps)| {
        let mut entrants = 0;
        let mut completed = vec![0; steps.len()];
        for ordered in by_visitor.values() {
            let mut cursor = 0;
            let mut first_at = 0;
            for &index in ordered {
                let event = &events[index];
                let step = &steps[cursor];
                if event.4 != step.0 || event.5 != step.1 {
                    continue;
                }
                if cursor == 0 {
                    first_at = event.1;
                    entrants += 1;
                } else if event.1 - first_at > within_seconds * 1_000_000 {
                    break;
                }
                completed[cursor] += 1;
                cursor += 1;
                if cursor == steps.len() {
                    break;
                }
            }
        }
        (entrants, completed)
    }).collect();
    (visitors, sessions, metrics, counts)
}

#[pyfunction]
#[pyo3(signature = (*, events, inactivity_seconds, funnels))]
fn project_batch(
    py: Python<'_>,
    events: Vec<Event>,
    inactivity_seconds: i64,
    funnels: Vec<Funnel>,
) -> PyResult<Projection> {
    if inactivity_seconds < 0 || inactivity_seconds > 86_400 {
        return Err(PyValueError::new_err("invalid inactivity seconds"));
    }
    if funnels.iter().any(|(seconds, steps)| {
        *seconds < 0 || *seconds > 2_592_000 || steps.is_empty()
    }) {
        return Err(PyValueError::new_err("invalid funnel window or steps"));
    }
    // Driver timestamps are exact Python datetime microseconds, inside i64 bounds.
    // Validate private callers too, keeping time subtraction panic-free.
    const MAX_INSTANT: i64 = 400_000_000_000_000_000;
    if events.iter().any(|event| !(-MAX_INSTANT..=MAX_INSTANT).contains(&event.1)) {
        return Err(PyValueError::new_err("event instant is outside datetime bounds"));
    }
    Ok(py.detach(move || project(events, inactivity_seconds * 1_000_000, funnels)))
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(project_batch, module)?)?;
    Ok(())
}
