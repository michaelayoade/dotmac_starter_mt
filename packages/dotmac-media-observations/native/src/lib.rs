use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
type Key = (String, String, String, String);

#[pyfunction]
fn cycle_children(py: Python<'_>, edges: Vec<(Key, String)>, active: Vec<Key>) -> Vec<Key> {
    py.detach(move || {
        let active: HashSet<_> = active.into_iter().collect();
        let mut graph = HashMap::new();
        for (child, parent) in edges {
            let parent = (child.0.clone(), child.1.clone(), child.2.clone(), parent);
            if active.contains(&child) && active.contains(&parent) { graph.insert(child, parent); }
        }
        let mut done = HashSet::new();
        let mut cycles = Vec::new();
        for start in graph.keys() {
            if done.contains(start) { continue; }
            let mut path: Vec<Key> = Vec::new();
            let mut positions = HashMap::new();
            let mut current = start.clone();
            while let Some(parent) = graph.get(&current) {
                if done.contains(&current) { break; }
                if let Some(&position) = positions.get(&current) {
                    cycles.extend(path[position..].iter().cloned());
                    break;
                }
                positions.insert(current.clone(), path.len());
                path.push(current);
                current = parent.clone();
            }
            done.extend(path);
        }
        cycles
    })
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(cycle_children, module)?)?;
    Ok(())
}
